"""ARQ entry points for long-running model evidence evaluations."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session_factory
from app.model_eval.engine import run_context_ladder, run_qualification
from app.models import ModelEvalRun


async def _mark_failed(run_id: uuid.UUID, code: str) -> None:
    async with async_session_factory() as db:
        run = (
            await db.execute(select(ModelEvalRun).where(ModelEvalRun.id == run_id))
        ).scalar_one_or_none()
        if run is None:
            return
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.result_summary = {
            **(run.result_summary or {}),
            "execution_complete": False,
            "error": code,
        }
        await db.commit()


async def run_model_qualification_job(ctx, run_id: str) -> dict:
    del ctx
    parsed_id = uuid.UUID(run_id)
    try:
        async with async_session_factory() as db:
            run = (
                await db.execute(select(ModelEvalRun).where(ModelEvalRun.id == parsed_id))
            ).scalar_one_or_none()
            if run is None:
                return {"status": "gone"}
            if run.status == "cancelled" or run.cancel_requested:
                return {"status": "cancelled"}
            if run.status not in {"queued", "running"}:
                return {"status": "skipped", "run_status": run.status}
            return await run_qualification(db, run, force=bool(run.force_requested))
    except Exception as exc:
        await _mark_failed(parsed_id, f"qualification_job_exception:{type(exc).__name__}")
        return {"status": "failed", "error": type(exc).__name__}


async def run_model_context_certification_job(ctx, run_id: str) -> dict:
    del ctx
    parsed_id = uuid.UUID(run_id)
    try:
        async with async_session_factory() as db:
            run = (
                await db.execute(select(ModelEvalRun).where(ModelEvalRun.id == parsed_id))
            ).scalar_one_or_none()
            if run is None:
                return {"status": "gone"}
            if run.status == "cancelled" or run.cancel_requested:
                return {"status": "cancelled"}
            if run.status not in {"queued", "running"}:
                return {"status": "skipped", "run_status": run.status}
            return await run_context_ladder(db, run, force=bool(run.force_requested))
    except Exception as exc:
        await _mark_failed(parsed_id, f"context_job_exception:{type(exc).__name__}")
        return {"status": "failed", "error": type(exc).__name__}


__all__ = ["run_model_qualification_job", "run_model_context_certification_job"]
