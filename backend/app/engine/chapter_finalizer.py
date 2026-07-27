"""Atomic final chapter snapshot + Canon (AI__.md v3.0 §8.3 / B-06).

Single transaction:
- FOR UPDATE chapter + xact advisory lock
- finalization_key idempotency
- immutable ChapterVersion(kind=final)
- Scene/Paragraph canon
- validated StoryEvent + L1 + L4
- search index + book stats
- chapter finalized_version + status
"""
from __future__ import annotations

import copy
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update, text, func, delete

from app.database import async_session_factory
from app.models import (
    Chapter,
    ChapterVersion,
    Scene,
    Paragraph,
    Book,
    StoryEvent,
    MemoryL1ChapterLedger,
    MemoryL4StateSnapshot,
    ChapterRun,
)
from app.models.tables import ChapterStateEvent, SceneSearchDocument
from app.state_machine import ChapterState
from app.engine.chinese_tokenizer import tokenize_for_search
from app.engine.final_artifact import (
    FinalArtifact,
    FinalSceneArtifact,
    build_final_artifact,
    finalization_key,
    canon_candidates_hash,
    sha256_text,
    SCENE_JOIN,
)
from app.engine.memory import _latest_l4_state, compute_source_hash

logger = logging.getLogger("novelforge.finalizer")


# Back-compat export used by pipeline historically
@dataclass
class FinalScene:
    scene_no: int
    content: str
    scene_id: uuid.UUID | None = None
    summary: str = ""
    pov_character_id: uuid.UUID | None = None


@dataclass
class FinalSnapshotResult:
    ok: bool
    version: int = 0
    word_count: int = 0
    content_hash: str = ""
    error: str | None = None
    idempotent: bool = False
    finalization_key: str | None = None


def _sha(s: str) -> str:
    return sha256_text(s)


def _chapter_lock_key(chapter_id: uuid.UUID) -> int:
    h = hashlib.sha256(f"finalize:{chapter_id}".encode()).digest()
    return int.from_bytes(h[:8], "big") % (2**63 - 1)


def _validate_event_against_paragraphs(
    evt: dict,
    para_by_key: dict[str, Paragraph],
) -> str | None:
    cert = evt.get("certainty")
    if cert != "explicit":
        return "certainty_not_explicit"
    key = evt.get("evidence_paragraph_key") or (
        (evt.get("evidence_paragraph_keys") or [None])[0]
    )
    if key and key in para_by_key:
        para = para_by_key[key]
        eh = evt.get("evidence_hash")
        if eh and eh != para.content_hash:
            return "evidence_hash_mismatch"
        return None
    # If no key, require evidence excerpt substring of some paragraph
    excerpt = (evt.get("evidence") or evt.get("evidence_excerpt") or "").strip()
    if excerpt and any(excerpt in (p.content or "") for p in para_by_key.values()):
        return None
    if not key:
        # Soft: allow events without evidence key when no paragraphs indexed
        if not para_by_key:
            return None
        return "evidence_key_missing"
    return "evidence_key_not_found"


async def commit_final_chapter_snapshot(
    *,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    expected_previous_version: int,
    final_content: str | None = None,
    final_scenes: list | None = None,
    final_artifact: FinalArtifact | None = None,
    validated_events: list[dict] | None = None,
    source_run_ids: list[uuid.UUID] | None = None,
    chapter_run_id: uuid.UUID | None = None,
    outline_node_id: uuid.UUID,
    outline_version_id: uuid.UUID | None = None,
    title: str,
    chapter_no: int,
    pipeline_version: str = "pipeline-v2",
    worker_id: str | None = None,
) -> FinalSnapshotResult:
    """Atomic finalize + canon. Fail closed; no partial writes."""
    events = list(validated_events or [])

    # Build artifact
    if final_artifact is None:
        scene_dicts = []
        if final_scenes:
            for sc in final_scenes:
                if isinstance(sc, FinalScene):
                    scene_dicts.append(
                        {
                            "scene_no": sc.scene_no,
                            "content": sc.content,
                            "summary": sc.summary,
                            "pov_character_id": sc.pov_character_id,
                            "scene_id": sc.scene_id,
                        }
                    )
                elif isinstance(sc, FinalSceneArtifact):
                    scene_dicts.append(
                        {
                            "scene_no": sc.scene_no,
                            "content": sc.content,
                            "summary": sc.summary,
                            "pov_character_id": sc.pov_character_id,
                            "scene_id": sc.scene_id,
                        }
                    )
                elif isinstance(sc, dict):
                    scene_dicts.append(sc)
        elif final_content:
            scene_dicts = [{"scene_no": 1, "content": final_content, "summary": ""}]
        final_artifact = build_final_artifact(scene_dicts, joined_content=final_content)

    err = final_artifact.validate_integrity()
    if err:
        return FinalSnapshotResult(ok=False, error=err)

    content_hash = final_artifact.joined_hash
    word_count = len(final_artifact.joined_content)
    source_run = (source_run_ids or [None])[0] or uuid.uuid4()
    candidates_hash = canon_candidates_hash(events)
    # Prefer stable chapter_run_id; never put random source_run into finalization_key
    run_id_str = str(chapter_run_id) if chapter_run_id else f"chapter:{chapter_id}"
    ov_str = str(outline_version_id) if outline_version_id else ""
    fkey = finalization_key(
        chapter_run_id=run_id_str,
        joined_hash=content_hash,
        canon_candidates_hash=candidates_hash,
        outline_version_id=ov_str,
        pipeline_version=pipeline_version,
    )

    async with async_session_factory() as db:
        # 1-2. lock chapter + xact advisory
        chapter = (
            await db.execute(
                select(Chapter).where(Chapter.id == chapter_id).with_for_update()
            )
        ).scalar_one_or_none()
        if not chapter:
            return FinalSnapshotResult(ok=False, error="chapter_not_found")

        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _chapter_lock_key(chapter_id)},
        )

        # 3. control / active run checks
        if chapter_run_id:
            run = (
                await db.execute(
                    select(ChapterRun).where(ChapterRun.id == chapter_run_id)
                )
            ).scalar_one_or_none()
            if run:
                ctrl = (run.control_requested or "none").lower()
                if ctrl in ("pause", "cancel"):
                    return FinalSnapshotResult(ok=False, error=f"control_{ctrl}")
                if worker_id and run.lease_owner and run.lease_owner != worker_id:
                    if run.lease_expires_at and run.lease_expires_at > datetime.now(
                        timezone.utc
                    ):
                        return FinalSnapshotResult(ok=False, error="lease_held_by_other")
                if chapter.active_run_id and chapter.active_run_id != chapter_run_id:
                    return FinalSnapshotResult(ok=False, error="active_run_mismatch")

        # 5. idempotent finalization_key (must run before already_finalized hard reject)
        existing_final = (
            await db.execute(
                select(ChapterVersion).where(
                    ChapterVersion.chapter_id == chapter_id,
                    ChapterVersion.finalization_key == fkey,
                )
            )
        ).scalar_one_or_none()
        if existing_final:
            if chapter.finalized_version != existing_final.version:
                chapter.status = ChapterState.FINALIZED.value
                chapter.finalized_version = existing_final.version
                await db.commit()
            elif chapter.status != ChapterState.FINALIZED.value:
                chapter.status = ChapterState.FINALIZED.value
                await db.commit()
            return FinalSnapshotResult(
                ok=True,
                version=existing_final.version,
                word_count=existing_final.word_count or word_count,
                content_hash=existing_final.content_hash or content_hash,
                idempotent=True,
                finalization_key=fkey,
            )

        if chapter.status == ChapterState.FINALIZED.value and chapter.finalized_version:
            # Already finalized under a different key — do not overwrite
            return FinalSnapshotResult(
                ok=False,
                error="already_finalized",
                version=chapter.finalized_version,
            )

        max_ver = (
            await db.execute(
                select(func.coalesce(func.max(ChapterVersion.version), 0)).where(
                    ChapterVersion.chapter_id == chapter_id
                )
            )
        ).scalar() or 0
        new_version = max(int(expected_previous_version) + 1, int(max_ver) + 1, 1)

        # 6. finalizing state (same txn)
        prev_status = chapter.status
        chapter.status = ChapterState.FINALIZING.value
        if hasattr(chapter, "state_version"):
            chapter.state_version = int(getattr(chapter, "state_version", 0) or 0) + 1
            chapter.last_transition_reason = "finalizer->finalizing"
        db.add(
            ChapterStateEvent(
                id=uuid.uuid4(),
                chapter_id=chapter_id,
                book_id=book_id,
                from_state=prev_status or ChapterState.STATE_EXTRACTING.value,
                to_state=ChapterState.FINALIZING.value,
                state_version=int(getattr(chapter, "state_version", 1) or 1),
                actor="finalizer",
                reason="enter finalizing",
                chapter_run_id=chapter_run_id,
            )
        )

        # Supersede prior scenes
        await db.execute(
            update(Scene)
            .where(
                Scene.chapter_id == chapter_id,
                Scene.canon_status.in_(["canon", "draft"]),
            )
            .values(canon_status="superseded")
        )

        # 7. immutable final version — never update existing row content
        clash = (
            await db.execute(
                select(ChapterVersion).where(
                    ChapterVersion.chapter_id == chapter_id,
                    ChapterVersion.version == new_version,
                )
            )
        ).scalar_one_or_none()
        if clash:
            new_version = int(max_ver) + 1

        db.add(
            ChapterVersion(
                id=uuid.uuid4(),
                book_id=book_id,
                chapter_id=chapter_id,
                version=new_version,
                content=final_artifact.joined_content,
                word_count=word_count,
                source_run_id=source_run,
                version_kind="final",
                content_hash=content_hash,
                chapter_run_id=chapter_run_id,
                finalization_key=fkey,
            )
        )

        # Clear accidental rows at new_version
        old_at_ver = (
            await db.execute(
                select(Scene.id).where(
                    Scene.chapter_id == chapter_id,
                    Scene.version == new_version,
                )
            )
        ).scalars().all()
        if old_at_ver:
            await db.execute(delete(Paragraph).where(Paragraph.scene_id.in_(old_at_ver)))
            await db.execute(
                delete(Scene).where(
                    Scene.chapter_id == chapter_id,
                    Scene.version == new_version,
                )
            )

        para_by_key: dict[str, Paragraph] = {}
        scene_id_by_no: dict[int, uuid.UUID] = {}

        for sc in final_artifact.scenes:
            sid = uuid.uuid4()
            scene_id_by_no[sc.scene_no] = sid
            pov = None
            if sc.pov_character_id:
                try:
                    pov = uuid.UUID(str(sc.pov_character_id))
                except ValueError:
                    pov = None
            db.add(
                Scene(
                    id=sid,
                    book_id=book_id,
                    chapter_id=chapter_id,
                    scene_no=sc.scene_no,
                    outline_node_id=outline_node_id,
                    content=sc.content,
                    content_hash=sc.content_hash,
                    canon_status="canon",
                    version=new_version,
                    pov_character_id=pov,
                )
            )
            # paragraphs from artifact (or re-split)
            paras = sc.paragraphs
            if not paras:
                raw = [p for p in sc.content.split("\n\n") if p.strip()]
                from app.engine.final_artifact import FinalParagraphArtifact

                paras = [
                    FinalParagraphArtifact(
                        paragraph_key=f"s{sc.scene_no:02d}-p{pi:04d}",
                        ordinal=pi,
                        content=ptxt,
                        content_hash=_sha(ptxt),
                    )
                    for pi, ptxt in enumerate(raw, start=1)
                ]
            for art_p in paras:
                # versioned key for uniqueness
                pkey = f"{art_p.paragraph_key}-v{new_version}"
                prow = Paragraph(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chapter_id=chapter_id,
                    scene_id=sid,
                    paragraph_key=pkey,
                    ordinal=art_p.ordinal,
                    content=art_p.content,
                    content_hash=art_p.content_hash,
                    version=new_version,
                )
                db.add(prow)
                para_by_key[art_p.paragraph_key] = prow
                para_by_key[pkey] = prow

            tokenized = tokenize_for_search(sc.content[:8000])
            excerpt = sc.content[:500]
            await db.execute(
                delete(SceneSearchDocument).where(
                    SceneSearchDocument.chapter_id == chapter_id,
                    SceneSearchDocument.scene_no == sc.scene_no,
                )
            )
            doc = SceneSearchDocument(
                id=uuid.uuid4(),
                book_id=book_id,
                chapter_id=chapter_id,
                scene_id=sid,
                chapter_no=chapter_no,
                scene_no=sc.scene_no,
                outline_node_id=outline_node_id,
                pov_character_id=pov,
                character_ids=[],
                location_ids=[],
                item_ids=[],
                plot_thread_ids=[],
                event_types=[],
                scene_summary=(sc.summary or excerpt)[:2000],
                evidence_excerpt=excerpt,
                search_text=sc.content[:8000],
                search_tsv="",
                canon_status="canon",
                content_hash=sc.content_hash,
                version=new_version,
            )
            db.add(doc)
            await db.flush()
            await db.execute(
                text(
                    """
                    UPDATE scene_search_documents
                    SET search_tsv = to_tsvector('simple', :tok)
                    WHERE id = :id
                    """
                ),
                {"tok": tokenized or sc.content[:8000], "id": str(doc.id)},
            )

        await db.flush()

        # 9-12. Canon events + L1 + L4 in same transaction
        merged: dict[uuid.UUID, dict] = {}
        entity_types: dict[uuid.UUID, str] = {}
        accepted_events: list[dict] = []

        for evt in events:
            if not isinstance(evt, dict):
                continue
            v_err = _validate_event_against_paragraphs(evt, para_by_key)
            if v_err:
                # Skip invalid candidates rather than failing whole finalize when
                # evidence keys are model-hallucinated; hard-fail only on certainty
                if v_err == "certainty_not_explicit":
                    continue
                logger.warning("skip canon event %s: %s", evt.get("event_key"), v_err)
                continue

            entity_id = evt.get("entity_id")
            if isinstance(entity_id, str):
                try:
                    entity_id = uuid.UUID(entity_id)
                except ValueError:
                    entity_id = None

            scene_no = int(evt.get("scene_no") or 1)
            sid = scene_id_by_no.get(scene_no) or next(iter(scene_id_by_no.values()), chapter_id)

            ekey = evt.get("evidence_paragraph_key")
            ekeys = list(evt.get("evidence_paragraph_keys") or [])
            if ekey and ekey not in ekeys:
                ekeys.append(ekey)
            # map to versioned keys if present
            ekeys_v = []
            for k in ekeys:
                if k in para_by_key:
                    ekeys_v.append(para_by_key[k].paragraph_key)
                else:
                    ekeys_v.append(k)

            subject_ids = list(evt.get("subject_entity_ids") or [])
            if entity_id and str(entity_id) not in [str(x) for x in subject_ids]:
                subject_ids.append(str(entity_id))

            db.add(
                StoryEvent(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chapter_id=chapter_id,
                    scene_id=sid if isinstance(sid, uuid.UUID) else uuid.uuid4(),
                    event_type=evt.get("entity_type") or evt.get("event_type") or "state_change",
                    subject_entity_ids=subject_ids,
                    object_entity_ids=evt.get("object_entity_ids") or [],
                    evidence_paragraph_keys=ekeys_v,
                    evidence_excerpt=(evt.get("evidence") or evt.get("evidence_excerpt") or "")[:2000],
                    certainty="explicit",
                    canon_status="canon",
                    source_run_id=source_run,
                    version=new_version,
                    before_state={"field": evt.get("field"), "value": evt.get("old_value")}
                    if evt.get("field")
                    else None,
                    after_state={"field": evt.get("field"), "value": evt.get("new_value")}
                    if evt.get("field")
                    else None,
                )
            )
            accepted_events.append(evt)

            if not entity_id:
                continue
            if entity_id not in merged:
                merged[entity_id] = await _latest_l4_state(
                    db, book_id, entity_id, chapter_no
                )
                entity_types[entity_id] = evt.get("entity_type") or "character"
            field = evt.get("field")
            new_value = evt.get("new_value")
            if field is not None:
                merged[entity_id][field] = new_value
            elif isinstance(new_value, dict):
                merged[entity_id].update(new_value)

        for entity_id, state in merged.items():
            db.add(
                MemoryL4StateSnapshot(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    entity_type=entity_types.get(entity_id, "character"),
                    entity_id=entity_id,
                    as_of_chapter=chapter_no,
                    state=copy.deepcopy(state),
                    version=new_version,
                    source_run_id=source_run,
                )
            )

        db.add(
            MemoryL1ChapterLedger(
                id=uuid.uuid4(),
                book_id=book_id,
                chapter_id=chapter_id,
                finalized_version=new_version,
                source_hash=compute_source_hash(str(accepted_events)),
                status="generated",
                ledger_json={"events": accepted_events, "finalization_key": fkey},
                source_run_id=source_run,
            )
        )

        # 14-16 finalized
        chapter.status = ChapterState.FINALIZED.value
        chapter.finalized_version = new_version
        chapter.title = title
        if hasattr(chapter, "state_version"):
            chapter.state_version = int(getattr(chapter, "state_version", 0) or 0) + 1
            chapter.last_transition_reason = "finalizer->finalized"
        db.add(
            ChapterStateEvent(
                id=uuid.uuid4(),
                chapter_id=chapter_id,
                book_id=book_id,
                from_state=ChapterState.FINALIZING.value,
                to_state=ChapterState.FINALIZED.value,
                state_version=int(getattr(chapter, "state_version", 1) or 1),
                actor="finalizer",
                reason="atomic finalize+canon",
                chapter_run_id=chapter_run_id,
            )
        )

        # 15 book stats
        book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one_or_none()
        if book:
            cnt = (
                await db.execute(
                    select(func.count())
                    .select_from(Chapter)
                    .where(
                        Chapter.book_id == book_id,
                        Chapter.status == ChapterState.FINALIZED.value,
                    )
                )
            ).scalar() or 0
            book.finalized_chapters = int(cnt)
            book.finalized_words = (book.finalized_words or 0) + word_count

        # 17 mark run succeeded if present
        if chapter_run_id:
            await db.execute(
                update(ChapterRun)
                .where(ChapterRun.id == chapter_run_id)
                .values(
                    status="succeeded",
                    current_step="finalize",
                    finished_at=datetime.now(timezone.utc),
                    error_code=None,
                    error_detail=None,
                )
            )

        await db.commit()
        return FinalSnapshotResult(
            ok=True,
            version=new_version,
            word_count=word_count,
            content_hash=content_hash,
            finalization_key=fkey,
        )
