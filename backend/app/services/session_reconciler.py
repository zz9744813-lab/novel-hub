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
from app.models import Chapter, ChapterRun, SessionAdvanceOutbox, WritingSession
from app.engine.state_transition import transition_chapter
from app.state_machine import ChapterState
from app.services.writing_session_controller import (
    ACTIVE_RUN_STATUSES,
    ACTIVE_SESSION_STATUSES,
)

logger = logging.getLogger("novelforge.session_reconciler")
RECONCILER_ID = f"{socket.gethostname()}:{os.getpid()}"
CLAIM_LIMIT = int(os.environ.get("SESSION_RECONCILE_LIMIT", "10"))
LEASE_SECONDS = 60
RUN_STALE_GRACE_SECONDS = int(os.environ.get("SESSION_RUN_STALE_GRACE_SECONDS", "120"))


def chapter_run_is_stale(run: ChapterRun, now: datetime) -> bool:
    """Return true for an abandoned RUNNING row with no live worker lease."""
    if run.status != "running":
        return False
    lease_until = run.lease_expires_at
    if lease_until is not None and lease_until.tzinfo is None:
        lease_until = lease_until.replace(tzinfo=timezone.utc)
    if run.lease_owner and lease_until is not None and lease_until >= now:
        return False

    last_seen = run.heartbeat_at or run.started_at or run.updated_at or run.created_at
    if last_seen is None:
        return True
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return last_seen < now - timedelta(seconds=RUN_STALE_GRACE_SECONDS)


async def _fail_chapter_of_orphaned_run(
    db,
    run: ChapterRun | None,
    chapter_id,
    *,
    reason: str,
) -> bool:
    """Sync the chapter to a legal failed state when its run is orphaned.

    Terminalizing only the ChapterRun while the Chapter stays in an
    intermediate state (e.g. reviewing) is the production P0 that blinded the
    next-chapter selector and recreated chapter 1 in a loop. The transition
    goes through transition_chapter() in the SAME transaction, leaving a
    ChapterStateEvent with actor/reason/run_id as audit evidence. Finalized
    chapters are never regressed; an already-failed chapter gets no duplicate
    event.
    """
    if chapter_id is None:
        return False
    chapter = (
        await db.execute(select(Chapter).where(Chapter.id == chapter_id).with_for_update())
    ).scalar_one_or_none()
    if chapter is None:
        return False
    current = chapter.status or "queued"
    if current in (ChapterState.FINALIZED.value, ChapterState.FAILED.value):
        return False
    await transition_chapter(
        chapter.id,
        ChapterState.FAILED,
        reason=reason,
        actor="session_reconciler",
        run_id=run.id if run is not None else None,
        db=db,
    )
    logger.warning(
        "reconciler synced chapter %s to failed (orphaned run %s)",
        chapter.id,
        run.id if run is not None else None,
    )
    return True


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
                        # The run row is gone but the chapter may still sit in
                        # an intermediate state — sync it before clearing the
                        # pointer so the selector can see and resume it.
                        await _fail_chapter_of_orphaned_run(
                            db,
                            None,
                            session.current_chapter_id,
                            reason="chapter run row vanished while chapter in progress",
                        )
                        session.current_chapter_run_id = None
                        session.current_chapter_id = None
                        session.current_chapter_no = None
                        # Poke the advance in the SAME pass: recovery must not
                        # depend on the next reconcile timer.
                        touched += 1
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
                    elif chapter_run_is_stale(run, now):
                        # An unhandled worker exception used to leave the run
                        # as RUNNING after its lease disappeared.  Such a row
                        # blocks the session forever and is never eligible for
                        # chapter outbox backfill.  Terminalize it, keep the
                        # chapter in lockstep (failed), then let the normal
                        # session outbox/controller recovery path decide
                        # whether to retry the chapter.
                        await _fail_chapter_of_orphaned_run(
                            db,
                            run,
                            run.chapter_id,
                            reason="orphaned chapter run terminalized: running row lost its worker lease",
                        )
                        run.status = "failed"
                        run.error_code = run.error_code or "orphaned_chapter_run"
                        run.error_detail = run.error_detail or {
                            "reason": "running row lost its worker lease",
                        }
                        run.finished_at = now
                        run.lease_owner = None
                        run.lease_expires_at = None
                        repaired += 1
                        touched += 1
                        logger.warning(
                            "terminalized stale chapter run id=%s session=%s chapter=%s",
                            run.id,
                            session.id,
                            run.chapter_id,
                        )
                    elif (
                        run.status == "failed"
                        and run.error_code == "orphaned_chapter_run"
                    ):
                        # Stock state left by earlier versions: the run was
                        # already terminalized as orphaned while its chapter
                        # stayed in an intermediate state (production P0).
                        # Sync the chapter in THIS pass and poke the advance
                        # so recovery never waits another reconcile cycle.
                        if await _fail_chapter_of_orphaned_run(
                            db,
                            run,
                            run.chapter_id,
                            reason="stock orphaned chapter run: syncing chapter to failed",
                        ):
                            repaired += 1
                        touched += 1
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
