"""v9.4 SessionController: deterministic session evaluation & advancement.

Single-writer model: every state change happens inside ONE transaction with
the fixed lock order Book → WritingSession → Chapter → ChapterRun → Outbox
(spec §10). The controller never enqueues Redis directly — it only writes
DB state and outbox rows; the outbox dispatcher turns those into ARQ jobs.

Lock order here:
  01 Book        (FOR UPDATE)
  02 WritingSession (FOR UPDATE)
  03 Chapter     (FOR UPDATE, when touched)
  04 ChapterRun  (FOR UPDATE)
  05 Outbox rows (inserted, never locked)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.editorial.session_metrics import (
    count_editorial_backlog,
    recent_consecutive_bad_first_rounds,
    recent_first_pass_yield,
)
from app.models import (
    Book,
    Chapter,
    ChapterTask,
    ChapterRun,
    ChapterVersion,
    OutlineNode,
    SessionAdvanceOutbox,
    WritingSession,
    WritingSessionEvent,
)
from app.services.next_chapter_selector import (
    NextChapterDecision,
    NextChapterSelectionError,
    select_next_chapter,
)
from app.workers.outbox_dispatcher import create_run_and_outbox

TERMINAL_SESSION_STATUSES = frozenset({"completed", "cancelled", "failed"})
# 'created' is included so a just-created session already owns the book
# (it is visible to concurrent create requests and the advance job flips it
# to 'running' on the first evaluation).
ACTIVE_SESSION_STATUSES = frozenset(
    {"created", "running", "pausing", "paused", "waiting_editorial", "blocked"}
)
ACTIVE_RUN_STATUSES = frozenset({"queued", "running", "retryable", "waiting_dependency"})

# CCNE Hard Block codes (spec §29)
CCNE_HARD_CODES = frozenset(
    {
        "CAUSAL_COMPILE_ERROR",
        "CAUSAL_PRECONDITION_FAILED",
        "HARD_EFFECT_MISSING",
        "HARD_EFFECT_CONTRADICTED",
        "ILLEGAL_KNOWLEDGE",
        "UNSUPPORTED_HARD_STATE_CHANGE",
    }
)
# Long-lived resource blockers (spec §30) — transient ones stay on the run retry path.
RESOURCE_HARD_CODES = frozenset(
    {"PROVIDER_UNAVAILABLE", "DISK_CRITICAL", "DATABASE_CRITICAL", "MODEL_UNAVAILABLE"}
)

DEFAULT_POLICY = {
    "schema_version": "writing-policy-v1",
    "max_unreviewed_ahead": 5,
    "stop_on_needs_human": True,
    "stop_on_causal_failure": True,
    "stop_on_quality_drop": True,
    "stop_on_resource_block": True,
    "quality_window_size": 10,
    "quality_min_sample": 5,
    "minimum_first_pass_yield": 0.70,
    "bad_first_round_verdicts": ["revise", "reject"],
    "consecutive_bad_limit": 2,
    "deadline_behavior": "finish_current_run",
}


@dataclass(frozen=True)
class SessionDecision:
    action: str  # start_next | wait_current | wait_editorial | pause | cancel | block | complete
    reason: str = ""
    detail: dict = field(default_factory=dict)


async def _record_event(
    db: AsyncSession,
    session_id: uuid.UUID,
    event_type: str,
    payload: dict | None = None,
    *,
    dedupe_key: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
) -> None:
    """Idempotent session event insert (spec §37)."""
    if dedupe_key is None:
        dedupe_key = f"{event_type}:{uuid.uuid4().hex}"
    await db.execute(
        pg_insert(WritingSessionEvent)
        .values(
            id=uuid.uuid4(),
            session_id=session_id,
            event_type=event_type,
            source_type=source_type,
            source_id=source_id,
            dedupe_key=dedupe_key,
            payload=payload or {},
        )
        .on_conflict_do_nothing(index_elements=["session_id", "dedupe_key"])
    )


async def _lock_book_and_session(
    db: AsyncSession, session_id: uuid.UUID
) -> tuple[Book, WritingSession | None]:
    """Lock Book first, then WritingSession (spec §10)."""
    meta = (
        await db.execute(
            select(WritingSession.id, WritingSession.book_id).where(
                WritingSession.id == session_id
            )
        )
    ).first()
    if meta is None:
        return None, None
    book = (
        await db.execute(
            select(Book).where(Book.id == meta.book_id).with_for_update()
        )
    ).scalar_one_or_none()
    session = (
        await db.execute(
            select(WritingSession).where(WritingSession.id == session_id).with_for_update()
        )
    ).scalar_one_or_none()
    return book, session


async def _current_chapter(db: AsyncSession, session: WritingSession) -> Chapter | None:
    if not session.current_chapter_id:
        return None
    return (
        await db.execute(
            select(Chapter).where(Chapter.id == session.current_chapter_id).with_for_update()
        )
    ).scalar_one_or_none()


async def _chapter_word_count(db: AsyncSession, chapter_id: uuid.UUID) -> int:
    """Approximate session word progress via the current finalized version."""
    ch = (
        await db.execute(select(Chapter).where(Chapter.id == chapter_id))
    ).scalar_one_or_none()
    if ch is None or ch.finalized_version is None:
        return 0
    row = (
        await db.execute(
            select(ChapterVersion.word_count).where(
                ChapterVersion.chapter_id == chapter_id,
                ChapterVersion.version == ch.finalized_version,
            )
        )
    ).scalar_one_or_none()
    return int(row or 0)


async def _clear_run_pointer(db: AsyncSession, session: WritingSession, now: datetime) -> None:
    session.current_chapter_id = None
    session.current_chapter_no = None
    session.current_chapter_run_id = None


async def _handle_terminal_run(
    db: AsyncSession, session: WritingSession, run: ChapterRun, now: datetime
) -> None:
    """Sanitize a terminal run pointer and tally progress (spec §18)."""
    if run.status == "succeeded":
        session.chapters_completed = int(session.chapters_completed or 0) + 1
        words = await _chapter_word_count(db, run.chapter_id)
        session.words_generated = int(session.words_generated or 0) + words
        await _record_event(
            db,
            session.id,
            "chapter_run_succeeded",
            {
                "run_id": str(run.id),
                "chapter_id": str(run.chapter_id),
                "chapter_no": run.chapter_no,
                "words": words,
            },
            dedupe_key=f"chapter_run_succeeded:{run.id}",
            source_type="chapter_run",
            source_id=str(run.id),
        )
    elif run.status == "needs_human":
        await _record_event(
            db,
            session.id,
            "chapter_run_failed",
            {"run_id": str(run.id), "status": run.status},
            dedupe_key=f"chapter_run_failed:{run.id}",
        )
    else:
        await _record_event(
            db,
            session.id,
            "chapter_run_failed",
            {"run_id": str(run.id), "status": run.status},
            dedupe_key=f"chapter_run_failed:{run.id}",
        )
    await _clear_run_pointer(db, session, now)


async def _prepare_chapter_and_run(
    db: AsyncSession,
    session: WritingSession,
    decision: NextChapterDecision,
    now: datetime,
) -> ChapterRun:
    """create_chapter path: materialize Chapter + ChapterRun + dispatch outbox."""
    chapter_id = decision.chapter_id
    if chapter_id is None:
        node = (
            await db.execute(
                select(OutlineNode).where(OutlineNode.id == decision.outline_node_id)
            )
        ).scalar_one_or_none()
        chapter = Chapter(
            id=uuid.uuid4(),
            book_id=session.book_id,
            chapter_no=decision.chapter_no,
            outline_node_id=decision.outline_node_id,
            status="queued",
            title=node.title if node else None,
        )
        db.add(chapter)
        await db.flush()
        chapter_id = chapter.id

    run = await create_run_and_outbox(
        db,
        book_id=session.book_id,
        chapter_id=chapter_id,
        chapter_no=decision.chapter_no,
        outline_version_id=decision.outline_version_id,
        request_id=f"ws:{session.id.hex}:{uuid.uuid4().hex[:12]}",
        created_by="writing_session",
        writing_session_id=session.id,
    )

    # Compat ChapterTask row for the legacy worker path
    chapter = (
        await db.execute(
            select(Chapter).where(Chapter.id == chapter_id).with_for_update()
        )
    ).scalar_one_or_none()
    if chapter is not None:
        chapter.active_run_id = run.id
    existing_task = (
        await db.execute(
            select(ChapterTask)
            .where(
                ChapterTask.book_id == session.book_id,
                ChapterTask.chapter_no == decision.chapter_no,
            )
            .order_by(ChapterTask.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_task is not None:
        existing_task.status = "queued"
        existing_task.lease_owner = None
        existing_task.lease_expires_at = None
        existing_task.heartbeat_at = None
        existing_task.last_error_code = None
        existing_task.last_error_detail = None
    else:
        db.add(
            ChapterTask(
                id=uuid.uuid4(),
                book_id=session.book_id,
                chapter_no=decision.chapter_no,
                status="queued",
            )
        )

    session.current_chapter_id = chapter_id
    session.current_chapter_no = decision.chapter_no
    session.current_chapter_run_id = run.id
    session.chapters_started = int(session.chapters_started or 0) + 1

    await _record_event(
        db,
        session.id,
        "chapter_run_attached",
        {
            "run_id": str(run.id),
            "chapter_id": str(chapter_id),
            "chapter_no": decision.chapter_no,
        },
        dedupe_key=f"chapter_run_attached:{run.id}",
        source_type="chapter_run",
        source_id=str(run.id),
    )
    return run


async def evaluate_session(db: AsyncSession, session: WritingSession) -> SessionDecision:
    """Evaluate one session under the fixed eval order (spec §16 + v9.5 preflight §29)."""
    if session.status in TERMINAL_SESSION_STATUSES:
        return SessionDecision(
            "complete" if session.status == "completed" else "cancel",
            "terminal state",
            {"status": session.status},
        )

    now = datetime.now(timezone.utc)
    policy = session.policy_snapshot or DEFAULT_POLICY

    # ── 04 control_requested (v9.6 §11: STOP has priority over preflight) ──
    if session.control_requested == "pause":
        if session.current_chapter_run_id:
            run = (
                await db.execute(
                    select(ChapterRun)
                    .where(ChapterRun.id == session.current_chapter_run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is not None and run.status in ACTIVE_RUN_STATUSES | {"paused"}:
                return SessionDecision("wait_current", "pause requested; current run finishing")
        session.status = "paused"
        session.control_requested = "none"
        session.paused_at = now
        await _record_event(db, session.id, "paused", {}, dedupe_key="pause_applied")
        return SessionDecision("pause", "session paused")

    if session.control_requested == "cancel":
        if session.current_chapter_run_id:
            run = (
                await db.execute(
                    select(ChapterRun)
                    .where(ChapterRun.id == session.current_chapter_run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is not None and run.status in ACTIVE_RUN_STATUSES | {"paused"}:
                return SessionDecision("wait_current", "cancel requested; current run finishing")
        session.status = "cancelled"
        session.control_requested = "none"
        session.completed_at = now
        await _record_event(db, session.id, "cancelled", {}, dedupe_key="cancel_applied")
        return SessionDecision("cancel", "session cancelled")

    # ── v9.6 preflight marker (spec §12): controller never runs network IO.
    # The session_preflight_job owns detection; it flips the status and
    # pokes the advance outbox when done. ──
    if session.status == "created" and session.model_preflight_status is None:
        session.model_preflight_status = "running"
        await _record_event(
            db, session.id, "model_preflight_started", {}, dedupe_key="model_preflight_started"
        )
        return SessionDecision("wait_current", "model preflight running")
    if session.status == "created" and session.model_preflight_status == "running":
        return SessionDecision("wait_current", "model preflight running (job)")

    # ── 05 deadline ──
    if session.deadline_at is not None and session.deadline_at <= now:
        if session.current_chapter_run_id:
            run = (
                await db.execute(
                    select(ChapterRun)
                    .where(ChapterRun.id == session.current_chapter_run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is not None and run.status in ACTIVE_RUN_STATUSES | {"paused"}:
                return SessionDecision("wait_current", "deadline reached; current run finishing")
        session.status = "completed"
        session.completed_at = now
        session.stop_reason = "deadline"
        await _record_event(db, session.id, "deadline_reached", {}, dedupe_key="deadline_reached")
        await _record_event(db, session.id, "completed", {}, dedupe_key="completed_deadline")
        return SessionDecision("complete", "deadline reached")

    # ── 06/07/08 current run ──
    if session.current_chapter_run_id:
        run = (
            await db.execute(
                select(ChapterRun)
                .where(ChapterRun.id == session.current_chapter_run_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if run is not None and run.status in ACTIVE_RUN_STATUSES:
            return SessionDecision("wait_current", f"current run {run.status}")
        if run is not None:
            await _handle_terminal_run(db, session, run, now)
        else:
            # stale pointer: safe to clear; reconciler also repairs this
            await _clear_run_pointer(db, session, now)
            await _record_event(
                db, session.id, "reconciler_repair",
                {"kind": "stale_run_pointer"},
                dedupe_key=f"repair_stale_pointer:{session.id}:{now.isoformat()}",
            )

    # ── 09 NEEDS_HUMAN ──
    chapter = await _current_chapter(db, session)
    if chapter is not None and chapter.status == "needs_human":
        session.status = "blocked"
        session.stop_reason = "needs_human"
        session.stop_detail = {"chapter_no": chapter.chapter_no}
        await _record_event(
            db,
            session.id,
            "needs_human_blocked",
            {"chapter_no": chapter.chapter_no},
            dedupe_key=f"needs_human_blocked:{chapter.id}",
        )
        return SessionDecision(
            "block", "NEEDS_HUMAN", {"chapter_no": chapter.chapter_no}
        )

    # ── 10 CCNE Hard Block ──
    if policy.get("stop_on_causal_failure", True):
        latest_run = (
            await db.execute(
                select(ChapterRun)
                .where(ChapterRun.book_id == session.book_id)
                .order_by(ChapterRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest_run is not None and (latest_run.error_code or "") in CCNE_HARD_CODES:
            session.status = "blocked"
            session.stop_reason = "causal_hard_failure"
            session.stop_detail = {
                "chapter_no": latest_run.chapter_no,
                "error_code": latest_run.error_code,
                "run_id": str(latest_run.id),
            }
            await _record_event(
                db,
                session.id,
                "causal_blocked",
                {"error_code": latest_run.error_code},
                dedupe_key=f"causal_blocked:{latest_run.id}",
            )
            return SessionDecision("block", "CCNE hard failure", {"error_code": latest_run.error_code})

    # ── 11 Resource Hard Block ──
    if policy.get("stop_on_resource_block", True):
        if chapter is not None and chapter.status == "resource_blocked":
            session.status = "blocked"
            session.stop_reason = "resource_blocked"
            session.stop_detail = {"chapter_no": chapter.chapter_no}
            await _record_event(
                db, session.id, "resource_blocked",
                {"chapter_no": chapter.chapter_no},
                dedupe_key=f"resource_blocked:{chapter.id}",
            )
            return SessionDecision("block", "resource hard block", {"chapter_no": chapter.chapter_no})
        if latest_run is not None and (latest_run.error_code or "") in RESOURCE_HARD_CODES:
            session.status = "blocked"
            session.stop_reason = "resource_blocked"
            session.stop_detail = {"error_code": latest_run.error_code}
            await _record_event(
                db, session.id, "resource_blocked",
                {"error_code": latest_run.error_code},
                dedupe_key=f"resource_blocked_run:{latest_run.id}",
            )
            return SessionDecision("block", "resource hard block", {"error_code": latest_run.error_code})

    # ── 12 Editorial backlog ──
    backlog_limit = int(policy.get("max_unreviewed_ahead", 5))
    backlog = await count_editorial_backlog(db, session.book_id)
    if backlog >= backlog_limit:
        session.status = "waiting_editorial"
        await _record_event(
            db,
            session.id,
            "waiting_editorial",
            {"backlog": backlog, "limit": backlog_limit},
            dedupe_key=f"waiting_editorial:{backlog}:{backlog_limit}",
        )
        return SessionDecision(
            "wait_editorial", f"editorial backlog {backlog}/{backlog_limit}",
            {"backlog": backlog, "limit": backlog_limit},
        )

    # ── 13 Recent first-pass yield ──
    if policy.get("stop_on_quality_drop", True):
        window_size = int(policy.get("quality_window_size", 10))
        min_sample = int(policy.get("quality_min_sample", 5))
        min_rate = float(policy.get("minimum_first_pass_yield", 0.70))
        quality = await recent_first_pass_yield(
            db, book_id=session.book_id, window_size=window_size
        )
        if quality["reviewed"] >= min_sample and quality["rate"] < min_rate:
            session.status = "blocked"
            session.stop_reason = "quality_drop"
            session.stop_detail = quality
            await _record_event(
                db,
                session.id,
                "quality_blocked",
                quality,
                dedupe_key=f"quality_blocked:{quality['reviewed']}:{quality['rate']}",
            )
            return SessionDecision(
                "block",
                f"recent first-pass yield {quality['rate']:.0%} < {min_rate:.0%}",
                quality,
            )

        # ── 14 Consecutive bad first rounds ──
        bad_limit = int(policy.get("consecutive_bad_limit", 2))
        consecutive = await recent_consecutive_bad_first_rounds(db, session.book_id)
        if consecutive >= bad_limit:
            session.status = "blocked"
            session.stop_reason = "consecutive_bad_reviews"
            session.stop_detail = {"consecutive_bad": consecutive}
            await _record_event(
                db,
                session.id,
                "quality_blocked",
                {"consecutive_bad": consecutive},
                dedupe_key=f"consecutive_bad:{consecutive}",
            )
            return SessionDecision(
                "block", f"{consecutive} consecutive bad first-round verdicts",
                {"consecutive_bad": consecutive},
            )

    # ── 15 select next chapter ──
    try:
        decision = await select_next_chapter(db, session.book_id)
    except NextChapterSelectionError as exc:
        code = (exc.detail or {}).get("code")
        chapter_no = (exc.detail or {}).get("chapter_no")
        if code == "OUTLINE_EXHAUSTED":
            session.status = "completed"
            session.completed_at = now
            session.stop_reason = "outline_exhausted"
            await _record_event(db, session.id, "outline_exhausted", {}, dedupe_key="outline_exhausted")
            await _record_event(db, session.id, "completed", {}, dedupe_key="completed_outline")
            return SessionDecision("complete", "outline exhausted")
        if code == "OUTLINE_NODE_MISSING":
            session.status = "blocked"
            session.stop_reason = "outline_node_missing"
            session.stop_detail = {"chapter_no": chapter_no}
            await _record_event(
                db, session.id, "needs_human_blocked",
                {"code": code, "chapter_no": chapter_no},
                dedupe_key=f"outline_node_missing:{chapter_no}",
            )
            return SessionDecision("block", "outline node missing", {"chapter_no": chapter_no})
        raise

    # ── 17 start next chapter ──
    if session.status != "running":
        session.status = "running"
        session.resumed_at = now
    await _prepare_chapter_and_run(db, session, decision, now)
    return SessionDecision("start_next", f"chapter {decision.chapter_no} started")


async def advance_writing_session(
    db: AsyncSession, session_id: uuid.UUID, completed_run_id: uuid.UUID | None = None
) -> dict:
    """Run one session evaluation; the caller owns the transaction/commit."""
    book, session = await _lock_book_and_session(db, session_id)
    if session is None:
        return {"action": "none", "reason": "session_not_found"}
    if book is None:
        session.status = "failed"
        session.stop_reason = "book_missing"
        await _record_event(db, session.id, "session_failed", {"reason": "book_missing"})
        return {"action": "cancel", "reason": "book missing", "session_id": str(session.id)}
    if session.control_requested in ("pause", "cancel"):
        await _record_event(
            db,
            session.id,
            "pause_requested" if session.control_requested == "pause" else "cancel_requested",
            {},
            dedupe_key=f"control_requested:{session.id}:{session.control_requested}",
        )
    decision = await evaluate_session(db, session)
    return {
        "action": decision.action,
        "reason": decision.reason,
        "detail": decision.detail,
        "session_id": str(session.id),
        "status": session.status,
    }


def serialize_session(session: WritingSession, *, backlog: int | None = None, quality: dict | None = None) -> dict:
    """API view of a session (spec §39 shape)."""
    return {
        "id": str(session.id),
        "book_id": str(session.book_id),
        "status": session.status,
        "control_requested": session.control_requested,
        "mode": session.mode,
        "requested_duration_minutes": session.requested_duration_minutes,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "deadline_at": session.deadline_at.isoformat() if session.deadline_at else None,
        "current_chapter_id": str(session.current_chapter_id) if session.current_chapter_id else None,
        "current_chapter_no": session.current_chapter_no,
        "current_chapter_run_id": str(session.current_chapter_run_id) if session.current_chapter_run_id else None,
        "chapters_started": session.chapters_started or 0,
        "chapters_completed": session.chapters_completed or 0,
        "words_generated": session.words_generated or 0,
        "stop_reason": session.stop_reason,
        "stop_detail": session.stop_detail,
        "policy_snapshot": session.policy_snapshot,
        "editorial_backlog": backlog,
        "editorial_backlog_limit": (session.policy_snapshot or {}).get("max_unreviewed_ahead"),
        "recent_first_pass": quality,
        "paused_at": session.paused_at.isoformat() if session.paused_at else None,
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


__all__ = [
    "ACTIVE_RUN_STATUSES",
    "ACTIVE_SESSION_STATUSES",
    "CCNE_HARD_CODES",
    "DEFAULT_POLICY",
    "RESOURCE_HARD_CODES",
    "SessionDecision",
    "TERMINAL_SESSION_STATUSES",
    "advance_writing_session",
    "evaluate_session",
    "serialize_session",
]
