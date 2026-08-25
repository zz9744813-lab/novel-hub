"""v9.4: writing session lifecycle service (create/pause/resume/cancel/extend).

API layer only mutates session state + outbox DB rows inside one transaction;
it never enqueues ARQ directly (spec §11, §35). Redis dispatch is the
dispatcher's job, failures are recovered by the outbox/stale-reclaim path.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time, timezone, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Book, SessionAdvanceOutbox, WritingSession
from app.schemas.writing_session import WritingSessionCreateRequest
from app.services.writing_session_controller import (
    DEFAULT_POLICY,
    ACTIVE_SESSION_STATUSES,
    TERMINAL_SESSION_STATUSES,
    _record_event,
    serialize_session,
)
from app.editorial.session_metrics import (
    count_editorial_backlog,
    recent_first_pass_yield,
)


def _parse_until_time(until_time: str) -> time:
    try:
        parsed = time.fromisoformat(until_time.strip())
    except ValueError:
        raise HTTPException(
            422,
            detail={"code": "INVALID_UNTIL_TIME", "message": "until_time 必须是 HH:MM"},
        )
    return parsed


def _resolve_until_datetime(until_time: str) -> datetime:
    """Local wall-clock parsed as the next absolute instant, then → UTC (spec §22)."""
    parsed = _parse_until_time(until_time)
    local_now = datetime.now()
    candidate = datetime.combine(local_now.date(), parsed)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def _build_policy(req: WritingSessionCreateRequest) -> dict:
    policy = dict(DEFAULT_POLICY)
    overrides = {
        "max_unreviewed_ahead": req.max_unreviewed_ahead,
        "quality_window_size": req.quality_window_size,
        "quality_min_sample": req.quality_min_sample,
        "minimum_first_pass_yield": req.minimum_first_pass_yield,
        "consecutive_bad_limit": req.consecutive_bad_limit,
        "stop_on_needs_human": req.stop_on_needs_human,
        "stop_on_causal_failure": req.stop_on_causal_failure,
        "stop_on_quality_drop": req.stop_on_quality_drop,
        "stop_on_resource_block": req.stop_on_resource_block,
    }
    for key, value in overrides.items():
        if value is not None:
            policy[key] = value
    return policy


def _touch_advance(db: AsyncSession, session_id: uuid.UUID, dedupe_hint: str | None = None) -> None:
    """Best-effort idempotent poke: ask the session to re-evaluate soon."""
    dedupe = dedupe_hint or f"session-touch:{session_id}:{uuid.uuid4().hex[:12]}"
    db.add(
        SessionAdvanceOutbox(
            id=uuid.uuid4(),
            writing_session_id=session_id,
            completed_run_id=None,
            event_type="advance_writing_session",
            dedupe_key=dedupe,
            payload={"session_id": str(session_id), "kind": "control"},
            status="pending",
            available_at=datetime.now(timezone.utc),
        )
    )


async def create_writing_session(
    db: AsyncSession,
    *,
    book_id: uuid.UUID,
    req: WritingSessionCreateRequest,
    idempotency_key: str | None = None,
) -> WritingSession:
    """Create a session atomically: idempotent + single-active guarantee (spec §9)."""
    book = (
        await db.execute(select(Book).where(Book.id == book_id).with_for_update())
    ).scalar_one_or_none()
    if book is None:
        raise HTTPException(404, detail={"code": "BOOK_NOT_FOUND", "message": "作品不存在"})

    if idempotency_key:
        existing = (
            await db.execute(
                select(WritingSession).where(
                    WritingSession.book_id == book_id,
                    WritingSession.create_idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    active = (
        await db.execute(
            select(WritingSession.id).where(
                WritingSession.book_id == book_id,
                WritingSession.status.in_(ACTIVE_SESSION_STATUSES),
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        raise HTTPException(
            409,
            detail={
                "code": "SESSION_OWNS_BOOK",
                "message": "该作品已有进行中的自动写作会话",
                "session_id": str(active),
            },
        )

    now = datetime.now(timezone.utc)
    if req.mode == "duration":
        deadline_at = now + timedelta(minutes=int(req.duration_minutes or 240))
    elif req.mode == "until_time":
        deadline_at = _resolve_until_datetime(req.until_time or "")
    else:
        deadline_at = None

    session = WritingSession(
        id=uuid.uuid4(),
        book_id=book_id,
        mode=req.mode,
        requested_duration_minutes=req.duration_minutes,
        started_at=now,
        deadline_at=deadline_at,
        status="created",
        control_requested="none",
        create_idempotency_key=idempotency_key,
        policy_version="writing-policy-v1",
        policy_snapshot=_build_policy(req),
    )
    db.add(session)
    await db.flush()

    await _record_event(
        db, session.id, "session_created",
        {"mode": req.mode, "policy_version": session.policy_version},
        dedupe_key="session_created",
    )
    # First advance also goes through the outbox (spec §11).
    await db.execute(
        pg_insert(SessionAdvanceOutbox)
        .values(
            id=uuid.uuid4(),
            writing_session_id=session.id,
            completed_run_id=None,
            event_type="advance_writing_session",
            dedupe_key=f"session-first:{session.id}",
            payload={"session_id": str(session.id), "kind": "first"},
            status="pending",
            available_at=now,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    return session


async def control_writing_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    action: str,  # pause | resume | cancel
    extend_minutes: int | None = None,
) -> WritingSession:
    session = (
        await db.execute(
            select(WritingSession).where(WritingSession.id == session_id).with_for_update()
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND", "message": "会话不存在"})
    if session.status in TERMINAL_SESSION_STATUSES:
        raise HTTPException(409, detail={"code": "SESSION_TERMINAL", "message": "会话已结束"})

    if extend_minutes:
        base = session.deadline_at or datetime.now(timezone.utc)
        session.deadline_at = base + timedelta(minutes=int(extend_minutes))
        await _record_event(
            db, session.id, "deadline_extended",
            {"extend_minutes": extend_minutes, "deadline_at": session.deadline_at.isoformat()},
        )
        return session

    if action == "pause":
        session.control_requested = "pause"
        await _record_event(db, session.id, "pause_requested", {}, dedupe_key="pause_requested")
        _touch_advance(db, session.id, dedupe_hint=f"session-touch-pause:{session.id}")
        return session

    if action == "cancel":
        # v9.6 §10: without a current run the session stops IMMEDIATELY
        # (no waiting for preflight / no next chapter).
        if session.current_chapter_run_id is None:
            session.status = "cancelled"
            session.control_requested = "none"
            session.completed_at = datetime.now(timezone.utc)
            session.stop_reason = "manual_stop"
            await _record_event(
                db, session.id, "cancelled",
                {"stop_reason": "manual_stop"},
                dedupe_key="cancel_manual_stop",
            )
            return session
        session.control_requested = "cancel"
        await _record_event(db, session.id, "cancel_requested", {}, dedupe_key="cancel_requested")
        _touch_advance(db, session.id, dedupe_hint=f"session-touch-cancel:{session.id}")
        return session

    if action == "resume":
        if session.status not in ("paused", "waiting_editorial", "blocked"):
            raise HTTPException(
                409,
                detail={"code": "SESSION_NOT_PAUSED", "message": "当前会话状态无需恢复"},
            )
        session.status = "running"
        session.control_requested = "none"
        session.resumed_at = datetime.now(timezone.utc)
        await _record_event(db, session.id, "session_resumed", {}, dedupe_key="session_resumed")
        _touch_advance(db, session.id, dedupe_hint=f"session-resume:{session.id}")
        return session

    raise HTTPException(422, detail={"code": "UNKNOWN_ACTION", "message": f"未知操作 {action}"})


def session_view(db: AsyncSession, session: WritingSession) -> dict:
    """Lightweight view helper used by the current/detail endpoints."""
    return serialize_session(session, backlog=None, quality=None)


async def poke_waiting_editorial_sessions(db: AsyncSession, book_id: uuid.UUID) -> int:
    """Editorial activity may release a waiting_editorial session (spec §24).

    Runs inside the caller's transaction: only inserts an advance outbox row,
    never Redis. The controller re-evaluates backlog before continuing.
    """
    rows = (
        await db.execute(
            select(WritingSession.id).where(
                WritingSession.book_id == book_id,
                WritingSession.status == "waiting_editorial",
            )
        )
    ).scalars().all()
    for sid in rows:
        await db.execute(
            pg_insert(SessionAdvanceOutbox)
            .values(
                id=uuid.uuid4(),
                writing_session_id=sid,
                completed_run_id=None,
                event_type="advance_writing_session",
                dedupe_key=f"session-editorial-resume:{sid}:{uuid.uuid4().hex[:10]}",
                payload={"session_id": str(sid), "kind": "editorial_resumed"},
                status="pending",
                available_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )
    return len(rows)


async def session_current_view(db: AsyncSession, session: WritingSession) -> dict:
    backlog = await count_editorial_backlog(db, session.book_id)
    policy = session.policy_snapshot or {}
    quality = await recent_first_pass_yield(
        db, book_id=session.book_id, window_size=int(policy.get("quality_window_size", 10))
    )
    view = serialize_session(
        session,
        backlog=backlog,
        quality={
            "reviewed": quality["reviewed"],
            "good": quality["good"],
            "rate": round(quality["rate"], 2),
        },
    )
    # v9.6 §84: live run step + status for the desk stepper / mini bar
    current_step = None
    current_run_status = None
    if session.current_chapter_run_id:
        from app.models import ChapterRun

        run = (
            await db.execute(
                select(ChapterRun).where(ChapterRun.id == session.current_chapter_run_id)
            )
        ).scalar_one_or_none()
        if run is not None:
            current_step = run.current_step
            current_run_status = run.status
    view["current_step"] = current_step
    view["current_run_status"] = current_run_status
    return view
