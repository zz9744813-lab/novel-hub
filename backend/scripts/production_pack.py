#!/usr/bin/env python3
"""Validate or install a versioned NovelForge production pack."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.production_pack import (  # noqa: E402 - script bootstraps backend path
    ProductionPackValidationError,
    load_and_validate_pack,
    validate_pack,
)


DEFAULT_PACK = (
    Path(__file__).resolve().parents[1]
    / "production_packs"
    / "zhutian_hongyanlu"
    / "pack.json"
)


def _verified_reference_texts(pack, paths: list[str]) -> list[str]:
    """Read only reference files whose exact bytes are declared by the pack."""
    declared = {source.sha256: source.source_id for source in pack.sources}
    texts: list[str] = []
    for value in paths:
        path = Path(value).expanduser().resolve()
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        source_id = declared.get(digest)
        if source_id is None:
            raise ValueError(
                "reference file SHA-256 is not declared by the production pack: "
                f"path={path} sha256={digest}"
            )
        texts.append(raw.decode("utf-8-sig"))
    return texts


def _load_with_verified_references(path: str, references: list[str]):
    pack, base_report = load_and_validate_pack(path)
    if not references:
        return pack, base_report, []
    reference_texts = _verified_reference_texts(pack, references)
    report = validate_pack(pack, reference_texts=reference_texts)
    if not report.passed:
        raise ProductionPackValidationError(report)
    return pack, report, reference_texts


def _scan_output(pack_path: str, manuscript_path: str, references: list[str]) -> dict:
    from app.production_pack.release_gate import scan_text_reference_overlap

    if not references:
        raise ValueError("scan-output requires at least one --reference")
    _, _, reference_texts = _load_with_verified_references(pack_path, references)
    manuscript_file = Path(manuscript_path).expanduser().resolve()
    reference_files = [Path(path).expanduser().resolve() for path in references]
    manuscript_bytes = manuscript_file.read_bytes()
    reference_bytes = [path.read_bytes() for path in reference_files]
    report = scan_text_reference_overlap(
        manuscript_bytes.decode("utf-8-sig"),
        reference_texts,
    )
    report["manuscript_file_sha256"] = hashlib.sha256(manuscript_bytes).hexdigest()
    report["reference_file_sha256"] = [
        hashlib.sha256(value).hexdigest() for value in reference_bytes
    ]
    return report


async def _install(path: str, references: list[str]) -> dict:
    from app.database import async_session_factory
    from app.production_pack import install_production_pack

    pack, report, _ = _load_with_verified_references(path, references)
    async with async_session_factory() as db:
        result = await install_production_pack(db, pack)
        await db.commit()
    return {"validation": report.model_dump(mode="json"), "installation": result}


async def _start(path: str, references: list[str]) -> dict:
    from app.database import async_session_factory
    from app.models import WritingSession
    from app.production_pack import install_production_pack
    from app.production_pack.service import stable_id
    from app.schemas.writing_session import WritingSessionCreateRequest
    from app.services.writing_session_controller import (
        ACTIVE_SESSION_STATUSES,
        serialize_session,
    )
    from app.services.writing_session_service import (
        _touch_advance,
        control_writing_session,
        create_writing_session,
    )
    from sqlalchemy import select

    pack, report, _ = _load_with_verified_references(path, references)
    async with async_session_factory() as db:
        installation = await install_production_pack(db, pack)
        book_id = stable_id(pack.pack_id, "book", "root")
        sessions = (
            await db.execute(
                select(WritingSession)
                .where(WritingSession.book_id == book_id)
                .order_by(WritingSession.created_at.desc(), WritingSession.id.desc())
            )
        ).scalars().all()
        active = next(
            (item for item in sessions if item.status in ACTIVE_SESSION_STATUSES),
            None,
        )
        if active is not None:
            if active.status in {"paused", "waiting_editorial", "blocked"}:
                session = await control_writing_session(
                    db,
                    session_id=active.id,
                    action="resume",
                )
            else:
                session = active
                if active.status in {"created", "running"}:
                    # Operator start is also an idempotent recovery poke.  If a
                    # prior outbox delivery was lost during a process restart,
                    # the controller gets another chance without creating a
                    # competing writing session.
                    _touch_advance(db, active.id)
        else:
            predecessor = str(sessions[0].id) if sessions else "initial"
            session = await create_writing_session(
                db,
                book_id=book_id,
                req=WritingSessionCreateRequest(
                    mode="manual",
                    max_unreviewed_ahead=100,
                    quality_window_size=10,
                    quality_min_sample=5,
                    minimum_first_pass_yield=0.70,
                    consecutive_bad_limit=2,
                    stop_on_needs_human=True,
                    stop_on_causal_failure=True,
                    stop_on_quality_drop=True,
                    stop_on_resource_block=True,
                ),
                idempotency_key=(
                    f"prod:{pack.pack_id}:r{pack.revision}:"
                    f"{report.pack_sha256[:16]}:after:{predecessor}"
                ),
            )
        await db.commit()
    return {
        "validation": report.model_dump(mode="json"),
        "installation": installation,
        "writing_session": serialize_session(session),
    }


async def _audit(path: str, references: list[str], *, run_blind: bool) -> dict:
    from app.production_pack.release_gate import run_release_audit

    pack, _, _ = _load_with_verified_references(path, references)
    report = await run_release_audit(
        pack,
        reference_paths=references,
        run_blind=run_blind,
    )
    return report.model_dump(mode="json")


async def _status(path: str) -> dict:
    from app.database import async_session_factory
    from app.production_pack.release_gate import (
        collect_release_snapshot,
        evaluate_release_snapshot,
    )

    pack, _ = load_and_validate_pack(path)
    async with async_session_factory() as db:
        snapshot = await collect_release_snapshot(db, pack)
    report = evaluate_release_snapshot(pack, snapshot)
    return report.model_dump(mode="json")


async def _export(path: str, output: str) -> dict:
    from app.database import async_session_factory
    from app.models import ManuscriptReleaseAudit
    from app.production_pack.release_gate import (
        GATE_VERSION,
        collect_release_snapshot,
        manuscript_hash,
    )
    from app.production_pack.service import stable_id
    from sqlalchemy import select

    pack, _ = load_and_validate_pack(path)
    book_id = stable_id(pack.pack_id, "book", "root")
    async with async_session_factory() as db:
        snapshot = await collect_release_snapshot(db, pack)
        current_hash = manuscript_hash(snapshot.get("chapters") or [])
        audit = (
            await db.execute(
                select(ManuscriptReleaseAudit).where(
                    ManuscriptReleaseAudit.book_id == book_id,
                    ManuscriptReleaseAudit.manuscript_hash == current_hash,
                    ManuscriptReleaseAudit.gate_version == GATE_VERSION,
                    ManuscriptReleaseAudit.production_pack_id == pack.pack_id,
                    ManuscriptReleaseAudit.production_pack_revision == pack.revision,
                    ManuscriptReleaseAudit.production_pack_sha256 == pack.canonical_sha256(),
                    ManuscriptReleaseAudit.status == "passed",
                )
            )
        ).scalar_one_or_none()
        installed_matches = (
            snapshot.get("production_pack_id") == pack.pack_id
            and int(snapshot.get("production_pack_revision") or 0) == pack.revision
            and snapshot.get("production_pack_sha256") == pack.canonical_sha256()
        )
    if not installed_matches:
        raise RuntimeError("installed production pack does not match the export pack")
    if audit is None:
        raise RuntimeError("current manuscript has no passed release audit")
    chapters = sorted(
        snapshot.get("chapters") or [], key=lambda item: int(item["chapter_no"])
    )
    if len(chapters) != pack.book.target_chapters:
        raise RuntimeError("current manuscript is incomplete")
    pieces = [f"《{pack.book.title}》\n{pack.book.subtitle}\n"]
    for item in chapters:
        pieces.append(
            f"\n第{item['chapter_no']}章 {item['title']}\n\n{item['content'].strip()}\n"
        )
    manuscript = "".join(pieces)
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(manuscript, encoding="utf-8")
    temporary.replace(destination)
    return {
        "passed": True,
        "path": str(destination),
        "chapters": len(chapters),
        "characters": len(manuscript),
        "sha256": __import__("hashlib").sha256(manuscript.encode("utf-8")).hexdigest(),
        "release_audit_id": str(audit.id),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "validate", "install", "start", "status", "audit", "export", "scan-output"
        ),
    )
    parser.add_argument("--pack", default=str(DEFAULT_PACK))
    parser.add_argument(
        "--reference",
        action="append",
        default=[],
        help=(
            "Optional local UTF-8 reference. Its raw SHA-256 must be declared by "
            "the pack; prose is used only for 16-character overlap auditing"
        ),
    )
    parser.add_argument(
        "--deterministic-only",
        action="store_true",
        help="For audit: stop before the anonymous model review",
    )
    parser.add_argument(
        "--output",
        default="exports/zhutian-hongyanlu.txt",
        help="For export: destination UTF-8 text file",
    )
    parser.add_argument(
        "--manuscript",
        help="For scan-output: downloaded UTF-8 manuscript path",
    )
    args = parser.parse_args()

    try:
        if args.command == "validate":
            _, report, _ = _load_with_verified_references(args.pack, args.reference)
            payload = report.model_dump(mode="json")
        elif args.command == "install":
            payload = asyncio.run(_install(args.pack, args.reference))
        elif args.command == "start":
            payload = asyncio.run(_start(args.pack, args.reference))
        elif args.command == "status":
            payload = asyncio.run(_status(args.pack))
        elif args.command == "audit":
            payload = asyncio.run(
                _audit(
                    args.pack,
                    args.reference,
                    run_blind=not args.deterministic_only,
                )
            )
        elif args.command == "scan-output":
            if not args.manuscript:
                raise ValueError("scan-output requires --manuscript")
            payload = _scan_output(args.pack, args.manuscript, args.reference)
        else:
            payload = asyncio.run(_export(args.pack, args.output))
    except Exception as exc:  # noqa: BLE001 - CLI emits a machine-readable failure
        report = getattr(exc, "report", None)
        payload = {
            "passed": False,
            "error": type(exc).__name__,
            "detail": str(exc),
            "validation": report.model_dump(mode="json") if report else None,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.command == "audit":
        acceptable = payload.get("passed") or (
            args.deterministic_only and payload.get("status") == "deterministic_pass"
        )
        if not acceptable:
            return 1
    if args.command == "scan-output" and not payload.get("passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
