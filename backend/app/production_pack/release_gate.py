"""Full-manuscript deterministic and blind release gates.

The deterministic gate examines every finalized chapter and every continuity
artifact.  Only after it passes may a text-only, anonymous stratified sample be
sent to the existing review route.  Raw reference prose is never included in a
model context; optional overlap auditing is local and emits hashes only.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.caller import call_agent
from app.engine.chapter_target import parse_chapter_target_chars
from app.models import (
    Book,
    BookSetting,
    Chapter,
    ChapterVersion,
    DriftAuditReport,
    ManuscriptReleaseAudit,
    MemoryL1ChapterLedger,
    MemoryL2StageSummary,
    MemoryL3VolumeSummary,
    MemoryL4StateSnapshot,
    Scene,
    SceneReasoningContract,
    StoryEvent,
    StoryEventEdge,
    WritingSession,
)
from app.production_pack.contracts import ProductionPack
from app.production_pack.service import stable_id


GATE_VERSION = "manuscript-release-v1"
ACTIVE_SESSION_STATUSES = {
    "created",
    "running",
    "pausing",
    "paused",
    "waiting_editorial",
    "blocked",
}
_META_LEAK = re.compile(
    r"(PIPELINE_BLOCKED|作为AI|作为人工智能|以下是正文|字数统计|JSON Schema|```json)",
    re.IGNORECASE,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReleaseFinding(StrictModel):
    code: str
    severity: Literal["blocker", "warning"]
    message: str
    path: str | None = None


class ReleaseGateReport(StrictModel):
    passed: bool
    status: Literal["failed", "deterministic_pass", "passed"]
    gate_version: str = GATE_VERSION
    pack_id: str
    pack_revision: int
    pack_sha256: str
    manuscript_hash: str
    findings: list[ReleaseFinding] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    sample_chapter_nos: list[int] = Field(default_factory=list)
    blind_report: dict[str, Any] | None = None
    blind_run_id: str | None = None


def expected_l2_ranges(pack: ProductionPack) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for volume in pack.volumes:
        start = volume.chapter_from
        while start <= volume.chapter_to:
            end = min(start + 9, volume.chapter_to)
            ranges.append((start, end))
            start = end + 1
    return ranges


def expected_drift_ranges(pack: ProductionPack) -> list[tuple[int, int]]:
    """Return contiguous 30-chapter windows, including the manuscript tail."""
    return [
        (start, min(start + 29, pack.book.target_chapters))
        for start in range(1, pack.book.target_chapters + 1, 30)
    ]


def release_sample_chapters(pack: ProductionPack) -> list[int]:
    """Opening, midpoint and ending of every volume, deduplicated and ordered."""
    selected: set[int] = set()
    for volume in pack.volumes:
        selected.add(volume.chapter_from)
        selected.add((volume.chapter_from + volume.chapter_to) // 2)
        selected.add(volume.chapter_to)
    return sorted(selected)


def _content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def manuscript_hash(chapters: list[dict[str, Any]]) -> str:
    lines = [
        f"{item.get('chapter_no')}:{item.get('content_hash') or _content_hash(item.get('content') or '')}"
        for item in sorted(chapters, key=lambda value: int(value.get("chapter_no") or 0))
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _add(
    findings: list[ReleaseFinding],
    code: str,
    message: str,
    *,
    path: str | None = None,
    severity: Literal["blocker", "warning"] = "blocker",
) -> None:
    findings.append(
        ReleaseFinding(code=code, severity=severity, message=message, path=path)
    )


def _reference_overlap_findings(
    chapters: list[dict[str, Any]],
    reference_texts: list[str],
    *,
    ngram_size: int = 16,
    limit: int = 100,
) -> list[ReleaseFinding]:
    if not reference_texts:
        return []
    owners: dict[str, int] = {}
    for chapter in chapters:
        normalized = re.sub(r"\s+", "", chapter.get("content") or "")
        for index in range(max(0, len(normalized) - ngram_size + 1)):
            owners.setdefault(normalized[index : index + ngram_size], chapter["chapter_no"])
    matches: dict[tuple[int, str], None] = {}
    for source in reference_texts:
        normalized = re.sub(r"\s+", "", source)
        for index in range(max(0, len(normalized) - ngram_size + 1)):
            gram = normalized[index : index + ngram_size]
            chapter_no = owners.get(gram)
            if chapter_no is None:
                continue
            digest = hashlib.sha256(gram.encode("utf-8")).hexdigest()[:16]
            matches[(chapter_no, digest)] = None
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    return [
        ReleaseFinding(
            code="MANUSCRIPT_REFERENCE_NGRAM_OVERLAP",
            severity="blocker",
            message=f"{ngram_size}-character overlap hash={digest}",
            path=f"chapter[{chapter_no}]",
        )
        for chapter_no, digest in sorted(matches)
    ]


def scan_text_reference_overlap(
    manuscript_text: str,
    reference_texts: list[str],
    *,
    ngram_size: int = 16,
) -> dict[str, Any]:
    """Run the local-only final residue scan without returning source prose."""
    if not reference_texts:
        raise ValueError("at least one local reference is required")
    findings = _reference_overlap_findings(
        [{"chapter_no": 0, "content": manuscript_text or ""}],
        reference_texts,
        ngram_size=ngram_size,
    )
    return {
        "passed": not findings,
        "ngram_size": ngram_size,
        "manuscript_text_sha256": _content_hash(manuscript_text or ""),
        "reference_text_sha256": [
            _content_hash(reference) for reference in reference_texts
        ],
        "overlap_count": len(findings),
        "findings": [item.model_dump(mode="json") for item in findings],
    }


def evaluate_release_snapshot(
    pack: ProductionPack,
    snapshot: dict[str, Any],
    *,
    reference_texts: list[str] | None = None,
) -> ReleaseGateReport:
    findings: list[ReleaseFinding] = []
    chapters = sorted(
        list(snapshot.get("chapters") or []),
        key=lambda item: int(item.get("chapter_no") or 0),
    )
    pack_sha = pack.canonical_sha256()
    actual_hash = manuscript_hash(chapters)
    expected_numbers = list(range(1, pack.book.target_chapters + 1))
    actual_numbers = [int(item.get("chapter_no") or 0) for item in chapters]

    if snapshot.get("production_pack_id") != pack.pack_id:
        _add(findings, "PACK_ID_MISMATCH", "installed production pack id differs")
    if int(snapshot.get("production_pack_revision") or 0) != pack.revision:
        _add(findings, "PACK_REVISION_MISMATCH", "installed production pack revision differs")
    if snapshot.get("production_pack_sha256") != pack_sha:
        _add(findings, "PACK_SHA_MISMATCH", "installed production pack hash differs")
    if actual_numbers != expected_numbers:
        _add(
            findings,
            "CHAPTER_SEQUENCE",
            f"expected 1..{pack.book.target_chapters}, got {len(actual_numbers)} ordered rows",
        )

    target = parse_chapter_target_chars(pack.book.chapter_target_chars)
    total_chars = 0
    unique_content_hashes: set[str] = set()
    chapters_with_edges = 0
    for chapter in chapters:
        number = int(chapter.get("chapter_no") or 0)
        path = f"chapter[{number}]"
        content = chapter.get("content") or ""
        total_chars += len(content.strip())
        computed_hash = _content_hash(content)
        stored_hash = chapter.get("content_hash")
        if chapter.get("status") != "finalized" or chapter.get("version_kind") != "final":
            _add(findings, "CHAPTER_NOT_FINAL", "chapter lacks a finalized final version", path=path)
        if stored_hash != computed_hash:
            _add(findings, "CHAPTER_HASH_MISMATCH", "stored final content hash does not match", path=path)
        if stored_hash in unique_content_hashes:
            _add(findings, "DUPLICATE_CHAPTER_CONTENT", "another chapter has the same final hash", path=path)
        if stored_hash:
            unique_content_hashes.add(stored_hash)
        actual_length = len(content.strip())
        if not target.minimum_chars <= actual_length <= target.maximum_chars:
            _add(
                findings,
                "CHAPTER_LENGTH_CONTRACT",
                f"{actual_length} chars outside {target.minimum_chars}..{target.maximum_chars}",
                path=path,
            )
        if not (chapter.get("title") or "").strip():
            _add(findings, "CHAPTER_TITLE_MISSING", "final chapter title is empty", path=path)
        if _META_LEAK.search(content):
            _add(findings, "MANUSCRIPT_META_LEAK", "generation/meta marker appears in final prose", path=path)
        residues = [value for value in pack.reference_residue_denylist if value and value in content]
        if residues:
            _add(
                findings,
                "MANUSCRIPT_REFERENCE_RESIDUE",
                f"denylisted source residue count={len(residues)}",
                path=path,
            )
        scene_count = int(chapter.get("scene_count") or 0)
        contract_count = int(chapter.get("contract_count") or 0)
        if scene_count < 1:
            _add(findings, "CANON_SCENE_MISSING", "no canonical scene", path=path)
        if contract_count < scene_count:
            _add(
                findings,
                "SCENE_CONTRACT_INCOMPLETE",
                f"{contract_count} finalized contracts for {scene_count} canonical scenes",
                path=path,
            )
        if int(chapter.get("event_count") or 0) < 1:
            _add(findings, "STORY_EVENT_MISSING", "no canonical story event", path=path)
        if int(chapter.get("ledger_count") or 0) != 1:
            _add(findings, "L1_LEDGER_MISSING", "chapter must have exactly one L1 ledger", path=path)
        if int(chapter.get("edge_count") or 0) > 0:
            chapters_with_edges += 1

    lower_total = int(pack.book.target_chars * 0.92)
    upper_total = int(pack.book.target_chars * 1.08)
    if not lower_total <= total_chars <= upper_total:
        _add(
            findings,
            "MANUSCRIPT_LENGTH_CONTRACT",
            f"total {total_chars} chars outside {lower_total}..{upper_total}",
        )
    required_edge_chapters = int(pack.book.target_chapters * 0.75)
    if chapters_with_edges < required_edge_chapters:
        _add(
            findings,
            "CAUSAL_EDGE_COVERAGE",
            f"only {chapters_with_edges} chapters have finalized causal edges; require {required_edge_chapters}",
        )

    book = snapshot.get("book") or {}
    if not book:
        _add(findings, "BOOK_MISSING", "production-pack book is not installed")
    if int(book.get("finalized_chapters") or 0) != pack.book.target_chapters:
        _add(findings, "BOOK_FINALIZED_COUNT", "book finalized_chapters is not target")
    if int(book.get("finalized_words") or 0) != total_chars:
        _add(findings, "BOOK_FINALIZED_CHARS", "book finalized_words differs from final text total")

    expected_l2 = set(expected_l2_ranges(pack))
    l2_summaries = list(snapshot.get("l2_summaries") or [])
    actual_l2 = {
        (int(item["start"]), int(item["end"])) for item in l2_summaries
    }
    missing_l2 = sorted(expected_l2 - actual_l2)
    if missing_l2:
        _add(findings, "L2_STAGE_SUMMARY_MISSING", f"missing ranges={missing_l2}")
    extra_l2 = sorted(actual_l2 - expected_l2)
    if extra_l2:
        _add(
            findings,
            "L2_STAGE_SUMMARY_EXTRA",
            f"unexpected ranges={extra_l2}",
            severity="warning",
        )
    for item in l2_summaries:
        if item.get("status") != "generated" or item.get("has_error"):
            _add(
                findings,
                "L2_STAGE_SUMMARY_DEGRADED",
                f"summary {item.get('start')}-{item.get('end')} is not usable",
            )
    expected_l3 = {volume.volume_no for volume in pack.volumes}
    l3_summaries = list(snapshot.get("l3_summaries") or [])
    actual_l3 = {int(item["volume_no"]) for item in l3_summaries}
    if expected_l3 - actual_l3:
        _add(
            findings,
            "L3_VOLUME_SUMMARY_MISSING",
            f"missing volumes={sorted(expected_l3 - actual_l3)}",
        )
    for item in l3_summaries:
        if item.get("status") != "generated" or item.get("has_error"):
            _add(
                findings,
                "L3_VOLUME_SUMMARY_DEGRADED",
                f"volume {item.get('volume_no')} summary is not usable",
            )

    expected_drifts = set(expected_drift_ranges(pack))
    drift_by_range = {
        (int(item["start"]), int(item["end"])): item
        for item in snapshot.get("drift_reports") or []
    }
    if expected_drifts - set(drift_by_range):
        _add(
            findings,
            "DRIFT_AUDIT_MISSING",
            f"missing ranges={sorted(expected_drifts - set(drift_by_range))}",
        )
    for span, item in drift_by_range.items():
        if item.get("status") == "red" or item.get("redline_findings"):
            _add(findings, "DRIFT_AUDIT_RED", f"red drift audit at {span}")
        if any(
            isinstance(finding, dict)
            and finding.get("type") == "audit_service_failure"
            for finding in (item.get("yellow_findings") or [])
        ):
            _add(
                findings,
                "DRIFT_AUDIT_UNAVAILABLE",
                f"drift audit service failed at {span}",
            )

    expected_characters = {
        str(stable_id(pack.pack_id, "character", item.character_id))
        for item in pack.characters
    }
    actual_characters = {str(value) for value in snapshot.get("l4_character_ids") or []}
    if expected_characters - actual_characters:
        _add(
            findings,
            "L4_CHARACTER_STATE_MISSING",
            f"missing character states={len(expected_characters - actual_characters)}",
        )
    if int(snapshot.get("initial_l4_count") or 0) < len(pack.characters):
        _add(findings, "INITIAL_L4_BASELINE_MISSING", "chapter-0 character baselines incomplete")

    session_rows = snapshot.get("writing_sessions") or []
    active = [row for row in session_rows if row.get("status") in ACTIVE_SESSION_STATUSES]
    if active:
        _add(findings, "WRITING_SESSION_ACTIVE", "an autonomous writing session is still active")
    completed = [
        row
        for row in session_rows
        if row.get("status") == "completed"
        and row.get("stop_reason") == "outline_exhausted"
    ]
    if not completed:
        _add(
            findings,
            "WRITING_SESSION_NOT_EXHAUSTED",
            "no completed session proves the approved outline was exhausted; multiple restart sessions are allowed",
        )

    findings.extend(
        _reference_overlap_findings(chapters, reference_texts or [])
    )
    if not reference_texts:
        _add(
            findings,
            "REFERENCE_OUTPUT_AUDIT_NOT_RUN",
            "no local reference was supplied for final 16-character overlap audit",
            severity="warning",
        )

    blockers = [item for item in findings if item.severity == "blocker"]
    return ReleaseGateReport(
        passed=False,
        status="failed" if blockers else "deterministic_pass",
        pack_id=pack.pack_id,
        pack_revision=pack.revision,
        pack_sha256=pack_sha,
        manuscript_hash=actual_hash,
        findings=findings,
        counts={
            "chapters": len(chapters),
            "total_chars": total_chars,
            "chapters_with_causal_edges": chapters_with_edges,
            "l2_summaries": len(actual_l2),
            "l3_summaries": len(actual_l3),
            "drift_audits": len(drift_by_range),
            "blockers": len(blockers),
            "warnings": len(findings) - len(blockers),
        },
        sample_chapter_nos=release_sample_chapters(pack),
    )


async def collect_release_snapshot(
    db: AsyncSession,
    pack: ProductionPack,
) -> dict[str, Any]:
    book_id = stable_id(pack.pack_id, "book", "root")
    book = (
        await db.execute(select(Book).where(Book.id == book_id))
    ).scalar_one_or_none()
    if book is None:
        return {
            "production_pack_id": None,
            "production_pack_revision": 0,
            "production_pack_sha256": None,
            "book": {},
            "chapters": [],
            "l2_summaries": [],
            "l3_summaries": [],
            "drift_reports": [],
            "l4_character_ids": [],
            "initial_l4_count": 0,
            "writing_sessions": [],
        }

    settings = (
        await db.execute(select(BookSetting).where(BookSetting.book_id == book_id))
    ).scalars().all()
    setting_map = {item.key: item.value for item in settings}
    chapter_rows = (
        await db.execute(
            select(Chapter)
            .where(Chapter.book_id == book_id)
            .order_by(Chapter.chapter_no)
        )
    ).scalars().all()
    chapter_ids = [item.id for item in chapter_rows]
    version_rows = (
        await db.execute(
            select(ChapterVersion).where(ChapterVersion.chapter_id.in_(chapter_ids))
        )
    ).scalars().all() if chapter_ids else []
    version_by_key = {(item.chapter_id, item.version): item for item in version_rows}

    async def grouped_count(model, *, extra_where=(), distinct_column=None):
        if not chapter_ids:
            return {}
        count_expression = (
            func.count(func.distinct(distinct_column))
            if distinct_column is not None
            else func.count()
        )
        rows = (
            await db.execute(
                select(model.chapter_id, count_expression)
                .where(model.chapter_id.in_(chapter_ids), *extra_where)
                .group_by(model.chapter_id)
            )
        ).all()
        return {chapter_id: int(count) for chapter_id, count in rows}

    scene_counts = await grouped_count(
        Scene,
        extra_where=(Scene.canon_status == "canon",),
        distinct_column=Scene.scene_no,
    )
    contract_counts = await grouped_count(
        SceneReasoningContract,
        extra_where=(SceneReasoningContract.status == "finalized",),
        distinct_column=SceneReasoningContract.scene_no,
    )
    event_counts = await grouped_count(StoryEvent, extra_where=(StoryEvent.canon_status == "canon",))
    edge_counts = await grouped_count(StoryEventEdge)
    ledger_counts = await grouped_count(MemoryL1ChapterLedger)

    chapters: list[dict[str, Any]] = []
    for chapter in chapter_rows:
        version = version_by_key.get((chapter.id, chapter.finalized_version))
        chapters.append(
            {
                "chapter_no": chapter.chapter_no,
                "title": chapter.title,
                "status": chapter.status,
                "finalized_version": chapter.finalized_version,
                "version_kind": version.version_kind if version else None,
                "content": version.content if version else "",
                "content_hash": version.content_hash if version else None,
                "scene_count": scene_counts.get(chapter.id, 0),
                "contract_count": contract_counts.get(chapter.id, 0),
                "event_count": event_counts.get(chapter.id, 0),
                "edge_count": edge_counts.get(chapter.id, 0),
                "ledger_count": ledger_counts.get(chapter.id, 0),
            }
        )

    l2_rows = (
        await db.execute(
            select(MemoryL2StageSummary).where(
                MemoryL2StageSummary.book_id == book_id,
                MemoryL2StageSummary.outline_version == pack.revision,
            )
        )
    ).scalars().all()
    l3_rows = (
        await db.execute(
            select(MemoryL3VolumeSummary).where(
                MemoryL3VolumeSummary.book_id == book_id,
                MemoryL3VolumeSummary.outline_version == pack.revision,
            )
        )
    ).scalars().all()
    drift_rows = (
        await db.execute(
            select(DriftAuditReport)
            .where(DriftAuditReport.book_id == book_id)
            .order_by(DriftAuditReport.created_at, DriftAuditReport.id)
        )
    ).scalars().all()
    l4_rows = (
        await db.execute(
            select(MemoryL4StateSnapshot).where(
                MemoryL4StateSnapshot.book_id == book_id,
                MemoryL4StateSnapshot.entity_type == "character",
            )
        )
    ).scalars().all()
    session_rows = (
        await db.execute(
            select(WritingSession)
            .where(WritingSession.book_id == book_id)
            .order_by(WritingSession.created_at)
        )
    ).scalars().all()
    return {
        "production_pack_id": setting_map.get("production_pack_id"),
        "production_pack_revision": int(setting_map.get("production_pack_revision") or 0),
        "production_pack_sha256": setting_map.get("production_pack_sha256"),
        "book": {
            "finalized_chapters": book.finalized_chapters,
            "finalized_words": book.finalized_words,
        },
        "chapters": chapters,
        "l2_summaries": [
            {
                "start": item.chapter_range_start,
                "end": item.chapter_range_end,
                "status": item.status,
                "has_error": (
                    not isinstance(item.summary_json, dict)
                    or bool(item.summary_json.get("error"))
                ),
            }
            for item in l2_rows
        ],
        "l3_summaries": [
            {
                "volume_no": item.volume_no,
                "status": item.status,
                "has_error": (
                    not isinstance(item.summary_json, dict)
                    or bool(item.summary_json.get("error"))
                ),
            }
            for item in l3_rows
        ],
        "drift_reports": [
            {
                "start": item.chapter_range_start,
                "end": item.chapter_range_end,
                "status": item.status,
                "redline_findings": item.redline_findings or [],
                "yellow_findings": item.yellow_findings or [],
            }
            for item in drift_rows
        ],
        "l4_character_ids": sorted({str(item.entity_id) for item in l4_rows}),
        "initial_l4_count": sum(1 for item in l4_rows if item.as_of_chapter == 0),
        "writing_sessions": [
            {
                "status": item.status,
                "stop_reason": item.stop_reason,
                "chapters_completed": item.chapters_completed,
            }
            for item in session_rows
        ],
    }


def _blind_excerpt(content: str, max_chars: int = 1800) -> str:
    text = content or ""
    if len(text) <= max_chars:
        return text
    part = max_chars // 3
    middle = max(0, len(text) // 2 - part // 2)
    return "\n[…]\n".join((text[:part], text[middle : middle + part], text[-part:]))


async def _run_blind_review(
    pack: ProductionPack,
    snapshot: dict[str, Any],
    sample_chapter_nos: list[int],
) -> tuple[dict[str, Any], uuid.UUID | None]:
    chapter_by_no = {
        int(item["chapter_no"]): item for item in snapshot.get("chapters") or []
    }
    samples = [
        {
            "sample_id": f"S{index:02d}",
            "text": _blind_excerpt(chapter_by_no[number]["content"]),
        }
        for index, number in enumerate(sample_chapter_nos, start=1)
        if number in chapter_by_no
    ]
    payload = {
        "mode": "blind_manuscript_release_review",
        "isolation": (
            "你没有书名、作者、参考文本、大纲或写作提示。只按匿名正文证据评价，"
            "不得猜测来源，也不得因题材或成人关系本身扣分。"
        ),
        "rubric": [
            "开篇是否迅速建立可理解的欲望、阻力与选择",
            "因果链是否可追，关键转折是否由人物行动而非巧合产生",
            "主要人物是否有独立目标、拒绝权、后果与非主角中心关系",
            "对白能否区分角色，潜台词与叙述说明是否失衡",
            "高压、余波、信息释放和段落节奏是否有变化而非模板循环",
            "伏笔与兑现是否公平，卷尾是否形成不可逆状态和下一问题",
            "情感与制度选择是否有持续余波，结局是否回应核心命题",
            "是否存在AI元评论、机械总结、同构章法、空泛意象或重复措辞",
        ],
        "decision_rule": (
            "任何 blocker/critical/major 问题都必须 verdict=revise；"
            "只有正文样本不存在重大问题时才能 passed=true。"
        ),
        "anonymous_samples": samples,
    }
    run, result, meta = await call_agent(
        book_id=stable_id(pack.pack_id, "book", "root"),
        agent_role="review_agent",
        user_content=json.dumps(payload, ensure_ascii=False),
    )
    meta = meta or {}
    run_id = getattr(run, "id", None)
    if not isinstance(result, dict):
        return {
            "passed": False,
            "issues": [
                {
                    "issue_id": "blind_review_service_failure",
                    "severity": "critical",
                    "category": "service_error",
                    "message": meta.get("block_reason") or meta.get("error") or "empty review",
                }
            ],
        }, run_id
    bad = {"blocker", "critical", "major"}
    issues = result.get("issues") or []
    if not isinstance(issues, list):
        issues = [
            {
                "issue_id": "blind_review_invalid_issues",
                "severity": "critical",
                "category": "service_error",
                "message": "review issues must be a list",
            }
        ]
    explicit = result.get("passed")
    if explicit is None:
        explicit = str(result.get("verdict") or "").lower() == "pass"
    else:
        explicit = explicit is True or str(explicit).lower() in {"true", "pass", "passed"}
    passed = bool(explicit) and not any(
        isinstance(item, dict)
        and str(item.get("severity") or "").lower() in bad
        for item in issues
    )
    return {**result, "issues": issues, "passed": passed}, run_id


async def run_release_audit(
    pack: ProductionPack,
    *,
    reference_paths: list[str] | None = None,
    run_blind: bool = True,
) -> ReleaseGateReport:
    from app.database import async_session_factory

    reference_texts = [
        Path(path).expanduser().resolve().read_text(encoding="utf-8-sig")
        for path in (reference_paths or [])
    ]
    async with async_session_factory() as db:
        snapshot = await collect_release_snapshot(db, pack)
    report = evaluate_release_snapshot(
        pack,
        snapshot,
        reference_texts=reference_texts,
    )
    if not snapshot.get("book"):
        return report
    # Missing or degraded derived summaries are recoverable.  Rebuild them only
    # when every other deterministic gate is already clean, avoiding expensive
    # model calls for a manuscript that still has substantive blockers.
    repairable_codes = {
        "L2_STAGE_SUMMARY_MISSING",
        "L2_STAGE_SUMMARY_DEGRADED",
        "L3_VOLUME_SUMMARY_MISSING",
        "L3_VOLUME_SUMMARY_DEGRADED",
        "DRIFT_AUDIT_MISSING",
        "DRIFT_AUDIT_UNAVAILABLE",
    }
    blocker_codes = {
        item.code for item in report.findings if item.severity == "blocker"
    }
    if blocker_codes and blocker_codes <= repairable_codes:
        from app.agents.drift_audit import run_drift_audit
        from app.engine.memory_compiler import generate_l2, generate_l3

        async with async_session_factory() as db:
            for start, end in expected_l2_ranges(pack):
                await generate_l2(
                    db,
                    stable_id(pack.pack_id, "book", "root"),
                    start,
                    end,
                    outline_version=pack.revision,
                )
                await db.commit()
            for volume in pack.volumes:
                await generate_l3(
                    db,
                    stable_id(pack.pack_id, "book", "root"),
                    volume.volume_no,
                    outline_version=pack.revision,
                    chapter_start=volume.chapter_from,
                    chapter_end=volume.chapter_to,
                )
                await db.commit()
            for start, end in expected_drift_ranges(pack):
                await run_drift_audit(
                    db,
                    stable_id(pack.pack_id, "book", "root"),
                    start,
                    end,
                )
                await db.commit()
            snapshot = await collect_release_snapshot(db, pack)
        report = evaluate_release_snapshot(
            pack,
            snapshot,
            reference_texts=reference_texts,
        )
    blind_run_id = None
    if report.status == "deterministic_pass" and run_blind:
        book_id = stable_id(pack.pack_id, "book", "root")
        async with async_session_factory() as db:
            prior_pass = (
                await db.execute(
                    select(ManuscriptReleaseAudit).where(
                        ManuscriptReleaseAudit.book_id == book_id,
                        ManuscriptReleaseAudit.manuscript_hash == report.manuscript_hash,
                        ManuscriptReleaseAudit.gate_version == GATE_VERSION,
                        ManuscriptReleaseAudit.production_pack_sha256 == report.pack_sha256,
                        ManuscriptReleaseAudit.status == "passed",
                    )
                )
            ).scalar_one_or_none()
        if prior_pass is not None:
            report.blind_report = prior_pass.blind_report
            report.blind_run_id = (
                str(prior_pass.blind_run_id) if prior_pass.blind_run_id else None
            )
            report.status = "passed"
            report.passed = True
        else:
            blind, blind_run_id = await _run_blind_review(
                pack,
                snapshot,
                report.sample_chapter_nos,
            )
            report.blind_report = blind
            report.blind_run_id = str(blind_run_id) if blind_run_id else None
            if blind.get("passed"):
                report.status = "passed"
                report.passed = True
            else:
                report.status = "failed"
                _add(
                    report.findings,
                    "BLIND_REVIEW_FAILED",
                    "anonymous stratified manuscript review returned major issues",
                )
                report.counts["blockers"] = int(report.counts.get("blockers") or 0) + 1

    book_id = stable_id(pack.pack_id, "book", "root")
    async with async_session_factory() as db:
        existing = (
            await db.execute(
                select(ManuscriptReleaseAudit).where(
                    ManuscriptReleaseAudit.book_id == book_id,
                    ManuscriptReleaseAudit.manuscript_hash == report.manuscript_hash,
                    ManuscriptReleaseAudit.gate_version == GATE_VERSION,
                    ManuscriptReleaseAudit.production_pack_sha256 == report.pack_sha256,
                )
            )
        ).scalar_one_or_none()
        values = {
            "production_pack_id": pack.pack_id,
            "production_pack_revision": pack.revision,
            "production_pack_sha256": report.pack_sha256,
            "status": report.status,
            "sample_chapter_nos": report.sample_chapter_nos,
            "deterministic_report": report.model_dump(
                mode="json", exclude={"blind_report", "blind_run_id"}
            ),
            "blind_report": report.blind_report,
            "blind_run_id": blind_run_id,
            "completed_at": datetime.now(timezone.utc),
        }
        if existing is None:
            db.add(
                ManuscriptReleaseAudit(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    gate_version=GATE_VERSION,
                    manuscript_hash=report.manuscript_hash,
                    **values,
                )
            )
        elif existing.status != "passed":
            for key, value in values.items():
                setattr(existing, key, value)
        await db.commit()
    return report


__all__ = [
    "GATE_VERSION",
    "ReleaseFinding",
    "ReleaseGateReport",
    "collect_release_snapshot",
    "evaluate_release_snapshot",
    "expected_drift_ranges",
    "expected_l2_ranges",
    "manuscript_hash",
    "release_sample_chapters",
    "run_release_audit",
    "scan_text_reference_overlap",
]
