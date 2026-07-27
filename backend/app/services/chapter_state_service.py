"""Unique chapter state transition entry (AI__.md v3.0 §5 / PR-01).

Thin wrapper around engine.state_transition with the public service API
specified in the remediation doc. Callers that hold a session pass db=;
this module never commits when db is provided.
"""
from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.state_transition import (
    IllegalTransitionError,
    StateConflictError,
    transition_chapter as _transition_chapter,
)
from app.models import Chapter
from app.state_machine import ChapterState

__all__ = [
    "IllegalTransitionError",
    "StateConflictError",
    "transition_chapter",
]


async def transition_chapter(
    db: AsyncSession,
    *,
    chapter_id: uuid.UUID,
    chapter_run_id: uuid.UUID | None = None,
    expected_states: Iterable[ChapterState | str] | None = None,
    target_state: ChapterState | str,
    actor: str,
    reason_code: str,
    step_key: str | None = None,
    detail: dict | None = None,
) -> Chapter:
    expected_set: set[str] | None = None
    if expected_states is not None:
        expected_set = {
            s.value if isinstance(s, ChapterState) else str(s) for s in expected_states
        }
    reason = reason_code
    if step_key:
        reason = f"{reason_code}|step={step_key}"
    if detail:
        # keep reason short; detail lives on event via reason field only for now
        reason = f"{reason}|{str(detail)[:200]}"
    return await _transition_chapter(
        chapter_id,
        target_state,
        expected_states=expected_set,
        reason=reason,
        actor=actor,
        run_id=chapter_run_id,
        db=db,
        allow_same=True,
    )
