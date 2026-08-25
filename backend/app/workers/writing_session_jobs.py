"""v9.4: ARQ job wrappers for writing sessions (spec §13, §32)."""
from __future__ import annotations

import logging
import uuid

from app.database import async_session_factory

logger = logging.getLogger("novelforge.session_jobs")


async def advance_writing_session_job(ctx, session_id: str, run_id: str = ""):
    """One session evaluation. All state changes commit in this transaction."""
    try:
        from sqlalchemy import select

        from app.models import WritingSession
        from app.services.writing_session_controller import advance_writing_session

        # v9.6 §12: a session awaiting preflight is handed to the dedicated
        # preflight job (network IO, own transactions) before evaluating.
        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(WritingSession.status, WritingSession.model_preflight_status).where(
                        WritingSession.id == uuid.UUID(session_id)
                    )
                )
            ).first()
        if row is not None and row[0] == "created" and row[1] == "running":
            from app.model_autopilot.session_preflight_job import run_writing_session_model_preflight

            result = await run_writing_session_model_preflight(session_id)
            logger.info("preflight job session=%s result=%s", session_id, result)
            return result

        async with async_session_factory() as db:
            result = await advance_writing_session(db, uuid.UUID(session_id))
            await db.commit()
        if result.get("action") != "wait_current":
            logger.info(
                "advance session=%s action=%s reason=%s",
                session_id,
                result.get("action"),
                result.get("reason"),
            )
        return result
    except Exception as e:  # noqa: BLE001 - job must surface error to ARQ
        logger.warning("advance_writing_session failed id=%s: %s", session_id, e)
        raise


async def session_reconciler_tick(ctx):
    """Cron: reconcile active sessions (lease + repair, spec §33–§36)."""
    try:
        from app.services.session_reconciler import reconcile_sessions

        report = await reconcile_sessions()
        if report.get("claimed") or report.get("repaired") or report.get("touched"):
            logger.info("session_reconciler_tick: %s", report)
        return report
    except Exception as e:  # noqa: BLE001
        logger.warning("session_reconciler_tick failed: %s", e)
        return {"error": str(e)}
