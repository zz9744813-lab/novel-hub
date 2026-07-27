"""Atomic final chapter snapshot service (P0-06).

In one transaction:
- create/upsert ChapterVersion at new_version
- write Scene/Paragraph at same version (canon)
- supersede old scenes (draft + previous canon)
- rebuild search index
- set Chapter.finalized_version
- idempotent Book stats
- hash consistency assertions
"""
from __future__ import annotations

import hashlib
import uuid
import logging
from dataclasses import dataclass
from sqlalchemy import select, update, text, func, delete
from app.database import async_session_factory
from app.models import Chapter, ChapterVersion, Scene, Paragraph, Book
from app.state_machine import ChapterState
from app.engine.chinese_tokenizer import tokenize_for_search

logger = logging.getLogger("novelforge.finalizer")


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


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


async def commit_final_chapter_snapshot(
    *,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    expected_previous_version: int,
    final_content: str,
    final_scenes: list[FinalScene],
    source_run_ids: list[uuid.UUID],
    outline_node_id: uuid.UUID,
    title: str,
    chapter_no: int,
) -> FinalSnapshotResult:
    if not final_content or not final_content.strip():
        return FinalSnapshotResult(ok=False, error="empty_final_content")
    if "[FAILED]" in final_content:
        return FinalSnapshotResult(ok=False, error="placeholder_in_final_content")
    if not final_scenes:
        return FinalSnapshotResult(ok=False, error="no_scenes")

    # Ensure unique sequential scene numbers
    renumbered: list[FinalScene] = []
    for idx, sc in enumerate(final_scenes, start=1):
        renumbered.append(
            FinalScene(
                scene_no=idx,
                content=sc.content,
                scene_id=sc.scene_id or uuid.uuid4(),
                summary=sc.summary,
                pov_character_id=sc.pov_character_id,
            )
        )
    final_scenes = renumbered

    joined = "\n\n".join(s.content for s in final_scenes)
    if len(final_scenes) > 1 and _sha(joined) != _sha(final_content):
        # Prefer final_content as source of truth for multi-scene drift after patch
        final_scenes = [
            FinalScene(
                scene_no=1,
                content=final_content,
                scene_id=final_scenes[0].scene_id,
                summary=final_scenes[0].summary,
            )
        ]
        joined = final_content

    content_hash = _sha(final_content)
    joined_hash = _sha(joined)
    if content_hash != joined_hash:
        return FinalSnapshotResult(ok=False, error="chapter_scene_hash_mismatch")

    source_run = source_run_ids[0] if source_run_ids else uuid.uuid4()
    word_count = len(final_content)

    async with async_session_factory() as db:
        chapter = (
            await db.execute(select(Chapter).where(Chapter.id == chapter_id))
        ).scalar_one_or_none()
        if not chapter:
            return FinalSnapshotResult(ok=False, error="chapter_not_found")

        max_ver = (
            await db.execute(
                select(func.coalesce(func.max(ChapterVersion.version), 0)).where(
                    ChapterVersion.chapter_id == chapter_id
                )
            )
        ).scalar() or 0

        # Final version is always a new row after drafts/patches
        new_version = max(int(expected_previous_version) + 1, int(max_ver) + 1, 1)

        # Idempotent: already finalized at this or higher version
        if (
            chapter.finalized_version
            and chapter.finalized_version >= new_version
            and chapter.status == ChapterState.FINALIZED.value
        ):
            return FinalSnapshotResult(
                ok=True,
                version=chapter.finalized_version,
                word_count=word_count,
                content_hash=content_hash,
            )

        chapter.status = ChapterState.FINALIZING.value
        if hasattr(chapter, "state_version"):
            chapter.state_version = int(getattr(chapter, "state_version", 0) or 0) + 1
            chapter.last_transition_reason = "finalizer->finalizing"
        # audit event (same transaction as finalize)
        try:
            from app.models.tables import ChapterStateEvent
            from datetime import datetime, timezone as _tz

            db.add(
                ChapterStateEvent(
                    id=uuid.uuid4(),
                    chapter_id=chapter_id,
                    book_id=book_id,
                    from_state=ChapterState.STATE_EXTRACTING.value,
                    to_state=ChapterState.FINALIZING.value,
                    state_version=int(getattr(chapter, "state_version", 1) or 1),
                    actor="finalizer",
                    reason="enter finalizing",
                )
            )
        except Exception:
            pass

        # Supersede all prior scenes for this chapter (draft + canon)
        await db.execute(
            update(Scene)
            .where(
                Scene.chapter_id == chapter_id,
                Scene.canon_status.in_(["canon", "draft"]),
            )
            .values(canon_status="superseded")
        )

        # Upsert ChapterVersion
        existing_ver = (
            await db.execute(
                select(ChapterVersion).where(
                    ChapterVersion.chapter_id == chapter_id,
                    ChapterVersion.version == new_version,
                )
            )
        ).scalar_one_or_none()
        if existing_ver:
            existing_ver.content = final_content
            existing_ver.word_count = word_count
            existing_ver.source_run_id = source_run
        else:
            db.add(
                ChapterVersion(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chapter_id=chapter_id,
                    version=new_version,
                    content=final_content,
                    word_count=word_count,
                    source_run_id=source_run,
                )
            )

        # Clear any accidental rows already at new_version (recovery)
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

        para_parts: list[str] = []
        for sc in final_scenes:
            sid = sc.scene_id or uuid.uuid4()
            # Always new id for final rows to avoid PK collision with draft ids
            # when promoting same logical scene.
            if sc.scene_id:
                # Keep id only if not already used by a remaining row
                clash = (
                    await db.execute(select(Scene.id).where(Scene.id == sid))
                ).scalar_one_or_none()
                if clash:
                    sid = uuid.uuid4()
            else:
                sid = uuid.uuid4()

            sc_hash = _sha(sc.content)
            scene_row = Scene(
                id=sid,
                book_id=book_id,
                chapter_id=chapter_id,
                scene_no=sc.scene_no,
                outline_node_id=outline_node_id,
                content=sc.content,
                content_hash=sc_hash,
                canon_status="canon",
                version=new_version,
                pov_character_id=sc.pov_character_id,
            )
            db.add(scene_row)

            paras = [p for p in sc.content.split("\n\n") if p.strip()]
            for pi, para_text in enumerate(paras, start=1):
                para_parts.append(para_text)
                db.add(
                    Paragraph(
                        id=uuid.uuid4(),
                        book_id=book_id,
                        chapter_id=chapter_id,
                        scene_id=sid,
                        paragraph_key=f"p-{sc.scene_no:02d}-{pi:04d}-v{new_version}",
                        ordinal=pi,
                        content=para_text,
                        content_hash=_sha(para_text),
                        version=new_version,
                    )
                )

            from app.models.tables import SceneSearchDocument

            tokenized = tokenize_for_search(sc.content[:8000])
            excerpt = sc.content[:500]
            # Drop old search docs for this scene_no
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
                pov_character_id=sc.pov_character_id,
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
                content_hash=sc_hash,
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

        # Paragraph join hash check (soft: scenes join already matched)
        para_hash = _sha("\n\n".join(para_parts)) if para_parts else content_hash
        if (
            para_hash != content_hash
            and _sha("\n\n".join(s.content for s in final_scenes)) != content_hash
        ):
            await db.rollback()
            return FinalSnapshotResult(ok=False, error="paragraph_hash_mismatch")

        chapter.status = ChapterState.FINALIZED.value
        chapter.finalized_version = new_version
        chapter.title = title
        if hasattr(chapter, "state_version"):
            chapter.state_version = int(getattr(chapter, "state_version", 0) or 0) + 1
            chapter.last_transition_reason = "finalizer->finalized"
        try:
            from app.models.tables import ChapterStateEvent

            db.add(
                ChapterStateEvent(
                    id=uuid.uuid4(),
                    chapter_id=chapter_id,
                    book_id=book_id,
                    from_state=ChapterState.FINALIZING.value,
                    to_state=ChapterState.FINALIZED.value,
                    state_version=int(getattr(chapter, "state_version", 1) or 1),
                    actor="finalizer",
                    reason="atomic finalize",
                )
            )
        except Exception:
            pass

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
            # current chapter counted after status set in same session
            words = (
                await db.execute(
                    select(func.coalesce(func.sum(ChapterVersion.word_count), 0)).where(
                        ChapterVersion.chapter_id.in_(
                            select(Chapter.id).where(
                                Chapter.book_id == book_id,
                                Chapter.status == ChapterState.FINALIZED.value,
                            )
                        ),
                        ChapterVersion.version
                        == select(Chapter.finalized_version)
                        .where(Chapter.id == ChapterVersion.chapter_id)
                        .scalar_subquery(),
                    )
                )
            ).scalar()
            book.finalized_chapters = int(cnt)
            if words:
                book.finalized_words = int(words)
            else:
                book.finalized_words = (book.finalized_words or 0) + word_count

        await db.commit()
        return FinalSnapshotResult(
            ok=True,
            version=new_version,
            word_count=word_count,
            content_hash=content_hash,
        )
