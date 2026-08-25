"""v9.6: session model preflight as a standalone ARQ job (spec §12, §105).

The controller NEVER holds session locks across provider network IO. Flow:
created → (marker r) → this job (own transactions) → pass/blocked → poke outbox.
Every phase re-checks session.status / control_requested — a cancel during
preflight stops immediately and never starts a chapter.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session_factory
from app.models import SessionAdvanceOutbox, WritingSession
from app.v74_utils import ModelBindingService

logger = logging.getLogger("novelforge.session_preflight")


async def _session_state(session_id: uuid.UUID) -> tuple[str, str, bool] | None:
    """Read (status, control_requested, cancelled) without holding locks."""
    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(WritingSession.status, WritingSession.control_requested).where(
                    WritingSession.id == session_id
                )
            )
        ).first()
        if row is None:
            return None
        return row[0], row[1], row[0] in ("cancelled", "failed")


async def run_writing_session_model_preflight(session_id: str) -> dict:
    sid = uuid.UUID(session_id)

    state = await _session_state(sid)
    if state is None:
        return {"status": "gone"}
    status, control, cancelled = state
    if cancelled or status != "created":
        return {"status": status, "skipped": True}

    # 1. network detection runs WITHOUT holding any session lock (spec §12)
    from app.model_autopilot.preflight import bootstrap_catalog_and_probes

    try:
        await bootstrap_catalog_and_probes()
    except Exception as e:  # noqa: BLE001 - never cascade-fail the session
        logger.warning("preflight bootstrap failed: %s", e)

    # 2. re-check after IO — the user may have cancelled meanwhile
    state = await _session_state(sid)
    if state is None or state[2]:
        return {"status": state[0] if state else "gone", "skipped": True}

    # 3. write the preflight result in one short transaction
    from app.model_autopilot.preflight import run_model_preflight
    from app.services.writing_session_controller import _record_event

    async with async_session_factory() as db:
        session = (
            await db.execute(
                select(WritingSession).where(WritingSession.id == sid).with_for_update()
            )
        ).scalar_one_or_none()
        if session is None or session.status != "created" or session.status in ("cancelled", "failed"):
            return {"status": "skipped"}
        if session.control_requested != "none":
            return {"status": session.control_requested, "skipped": True}

        svc = ModelBindingService(db)
        binding = await svc.get_binding("draft_writer", session.book_id)
        preflight = await run_model_preflight(db, session=session, binding=binding)

        if preflight.get("status") == "blocked":
            session.status = "blocked"
            session.stop_reason = "model_preflight_failed"
            session.stop_detail = {"blockers": preflight.get("blockers", [])}
            session.model_preflight_status = "blocked"
            session.model_preflight_detail = preflight
            return_reason = "blocked"
        else:
            session.status = "running"
            session.model_preflight_status = "pass"
            await _record_event(
                db,
                session.id,
                "session_started",
                {"route_plan_id": preflight.get("route_plan_id")},
                dedupe_key="session_started",
            )
            # poke: next advance evaluation starts the first chapter
            await db.execute(
                pg_insert(SessionAdvanceOutbox)
                .values(
                    id=uuid.uuid4(),
                    writing_session_id=session.id,
                    completed_run_id=None,
                    event_type="advance_writing_session",
                    dedupe_key=f"session-preflight-done:{session.id}",
                    payload={"session_id": str(session.id), "kind": "preflight_done"},
                    status="pending",
                    available_at=datetime.now(timezone.utc),
                )
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
            )
            return_reason = "running"
        await db.commit()
    return {"status": return_reason}
