"""v9.4: session reconciler (spec §32–§36).

Periodic safety net (P0). Only repairs DATABASE state or backfills outbox rows;
it never enqueues Redis directly (spec §35). All progress still flows
DB → Outbox → Dispatcher → ARQ.
"""
from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session_factory
from app.editorial.session_metrics import count_editorial_backlog
from app.models import ChapterRun, SessionAdvanceOutbox, WritingSession
from app.services.writing_session_controller import (
    ACTIVE_RUN_STATUSES,
    ACTIVE_SESSION_STATUSES,
)

logger = logging.getLogger("novelforge.session_reconciler")
RECONCILER_ID = f"{socket.gethostname()}:{os.getpid()}"
CLAIM_LIMIT = int(os.environ.get("SESSION_RECONCILE_LIMIT", "10"))
LEASE_SECONDS = 60


async def reconcile_sessions() -> dict:
    report = {"claimed": 0, "repaired": 0, "touched": 0, "errors": 0}
    now = datetime.now(timezone.utc)

    # ── claim a lease batch, FOR UPDATE SKIP LOCKED (spec §33) ──
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(WritingSession)
                .where(
                    WritingSession.status.in_(ACTIVE_SESSION_STATUSES),
                    or_(
                        WritingSession.reconcile_lease_until.is_(None),
                        WritingSession.reconcile_lease_until < now,
                    ),
                )
                .order_by(WritingSession.last_reconciled_at.asc().nullsfirst())
                .limit(CLAIM_LIMIT)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        for s in rows:
            s.reconcile_lease_owner = RECONCILER_ID
            s.reconcile_lease_until = now + timedelta(seconds=LEASE_SECONDS)
            s.last_reconciled_at = now
        await db.commit()

    # ── process each lease in its own transaction ──
    for claimed in rows:
        try:
            async with async_session_factory() as db:
                session = (
                    await db.execute(
                        select(WritingSession)
                        .where(WritingSession.id == claimed.id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if session is None:
                    continue
                report["claimed"] += 1
                repaired = touched = 0

                # 1) stale run pointer: run row vanished
                if session.current_chapter_run_id:
                    run = (
                        await db.execute(
                            select(ChapterRun).where(
                                ChapterRun.id == session.current_chapter_run_id
                            )
                        )
                    ).scalar_one_or_none()
                    if run is None:
                        session.current_chapter_run_id = None
                        session.current_chapter_id = None
                        session.current_chapter_no = None
                        repaired += 1
                    elif run.status == "succeeded":
                        # 2) run succeeded but its advance outbox is missing (spec §36)
                        existing = (
                            await db.execute(
                                select(SessionAdvanceOutbox.id).where(
                                    SessionAdvanceOutbox.writing_session_id == session.id,
                                    SessionAdvanceOutbox.completed_run_id == run.id,
                                    SessionAdvanceOutbox.status.in_(
                                        ("pending", "dispatching", "dispatched")
                                    ),
                                )
                            )
                        ).scalar_one_or_none()
                        if existing is None:
                            await db.execute(
                                pg_insert(SessionAdvanceOutbox)
                                .values(
                                    id=uuid.uuid4(),
                                    writing_session_id=session.id,
                                    completed_run_id=run.id,
                                    event_type="advance_writing_session",
                                    dedupe_key=f"session-advance:{session.id}:{run.id}",
                                    payload={
                                        "session_id": str(session.id),
                                        "completed_run_id": str(run.id),
                                    },
                                    status="pending",
                                    available_at=datetime.now(timezone.utc),
                                )
                                .on_conflict_do_nothing(index_elements=["dedupe_key"])
                            )
                            repaired += 1
                    elif run.status not in ACTIVE_RUN_STATUSES | {"paused"}:
                        # terminal-but-uncleaned run: nudge controller to clear pointer
                        touched += 1
                else:
                    # 3) running session with no active run → re-evaluate soon
                    if session.status == "running":
                        touched += 1

                # 4) waiting_editorial may be resolved (spec §24)
                if session.status == "waiting_editorial":
                    limit = int((session.policy_snapshot or {}).get("max_unreviewed_ahead", 5))
                    backlog = await count_editorial_backlog(db, session.book_id)
                    if backlog < limit:
                        touched += 1

                # 5) control_requested resolvable without an active run
                if session.control_requested in ("pause", "cancel") and not session.current_chapter_run_id:
                    touched += 1

                # 6) deadline passed without an active run
                if (
                    session.deadline_at is not None
                    and session.deadline_at <= datetime.now(timezone.utc)
                    and not session.current_chapter_run_id
                ):
                    touched += 1

                if touched:
                    await db.execute(
                        pg_insert(SessionAdvanceOutbox)
                        .values(
                            id=uuid.uuid4(),
                            writing_session_id=session.id,
                            completed_run_id=None,
                            event_type="advance_writing_session",
                            dedupe_key=f"session-reconcile:{session.id}:{uuid.uuid4().hex[:10]}",
                            payload={"session_id": str(session.id), "kind": "reconcile"},
                            status="pending",
                            available_at=datetime.now(timezone.utc),
                        )
                        .on_conflict_do_nothing(index_elements=["dedupe_key"])
                    )

                report["repaired"] += repaired
                report["touched"] += touched
                await db.commit()
        except Exception as e:  # noqa: BLE001 - reconciler must never cascade-fail
            logger.warning("session reconcile failed id=%s: %s", claimed.id, e)
            report["errors"] += 1

    return report
