"""Unique chapter state write entry (P1 CORE-001 / CORE-002).

All pipeline / worker / API chapter status changes should go through
transition_chapter() so assert_transition is enforced and events are audited.
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.state_machine import ChapterState, assert_transition, can_transition
from app.models import Chapter
from app.models.tables import ChapterStateEvent

logger = logging.getLogger("novelforge.state")


class IllegalTransitionError(ValueError):
    pass


class StateConflictError(RuntimeError):
    """CAS / expected_states mismatch."""


async def transition_chapter(
    chapter_id: uuid.UUID,
    target_state: str | ChapterState,
    *,
    expected_states: set[str] | None = None,
    reason: str | None = None,
    actor: str = "system",
    run_id: uuid.UUID | None = None,
    db: AsyncSession | None = None,
    allow_same: bool = True,
) -> Chapter:
    """CAS transition with FOR UPDATE + state_version + event log.

    If ``db`` is provided, uses that session (caller commits).
    Otherwise opens a short-lived session and commits.
    """
    target = target_state.value if isinstance(target_state, ChapterState) else str(target_state)

    async def _run(session: AsyncSession) -> Chapter:
        result = await session.execute(
            select(Chapter).where(Chapter.id == chapter_id).with_for_update()
        )
        chapter = result.scalar_one_or_none()
        if not chapter:
            raise LookupError(f"Chapter {chapter_id} not found")

        current = chapter.status or ChapterState.QUEUED.value
        if expected_states is not None and current not in expected_states:
            raise StateConflictError(
                f"Chapter {chapter_id} status={current} not in expected={expected_states}"
            )

        if current == target:
            if allow_same:
                return chapter
            raise IllegalTransitionError(f"Already in {target}")

        try:
            assert_transition(ChapterState(current), ChapterState(target))
        except ValueError as e:
            # Soft-expand common re-queue paths used by API/worker
            if current == ChapterState.FINALIZED.value and target == ChapterState.QUEUED.value:
                pass  # explicit re-run
            elif not can_transition(ChapterState(current), ChapterState(target)):
                raise IllegalTransitionError(str(e)) from e

        prev_version = int(getattr(chapter, "state_version", 0) or 0)
        new_version = prev_version + 1
        now = datetime.now(timezone.utc)

        chapter.status = target
        if hasattr(chapter, "state_version"):
            chapter.state_version = new_version
        if hasattr(chapter, "state_changed_at"):
            chapter.state_changed_at = now
        if hasattr(chapter, "last_transition_reason"):
            chapter.last_transition_reason = (reason or "")[:500] or None

        session.add(
            ChapterStateEvent(
                id=uuid.uuid4(),
                chapter_id=chapter.id,
                book_id=chapter.book_id,
                from_state=current,
                to_state=target,
                state_version=new_version,
                actor=actor[:100],
                reason=(reason or "")[:1000] or None,
                run_id=run_id,
            )
        )
        logger.info(
            "chapter_transition chapter=%s %s -> %s v=%s actor=%s reason=%s",
            chapter_id,
            current,
            target,
            new_version,
            actor,
            reason,
        )
        return chapter

    if db is not None:
        return await _run(db)

    async with async_session_factory() as session:
        ch = await _run(session)
        await session.commit()
        # re-load minimal
        return ch
