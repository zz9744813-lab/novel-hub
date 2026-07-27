"""AgentRun / ChapterTask orphan reconciler (P1 CORE-005).

Marks long-running AgentRuns without completion as abandoned, and
requeues expired chapter task leases. Safe to call from worker startup
and periodically.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, update

from app.database import async_session_factory
from app.models import AgentRun, ChapterTask, Chapter
from app.state_machine import ChapterState

logger = logging.getLogger("novelforge.reconciler")

# Role max duration defaults (seconds). Override with env RECONCILE_RUN_MAX_AGE_SEC.
DEFAULT_RUN_MAX_AGE_SEC = int(os.environ.get("RECONCILE_RUN_MAX_AGE_SEC", str(2 * 3600)))
DEFAULT_LEASE_GRACE_SEC = int(os.environ.get("RECONCILE_LEASE_GRACE_SEC", "120"))


async def reconcile_orphan_agent_runs(
    max_age_sec: int | None = None,
) -> dict:
    """Mark AgentRun.status=running older than max_age as abandoned."""
    max_age = max_age_sec if max_age_sec is not None else DEFAULT_RUN_MAX_AGE_SEC
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age)
    abandoned = 0
    async with async_session_factory() as db:
        result = await db.execute(
            select(AgentRun).where(
                AgentRun.status == "running",
                AgentRun.started_at < cutoff,
            )
        )
        runs = list(result.scalars().all())
        now = datetime.now(timezone.utc)
        for run in runs:
            run.status = "abandoned"
            run.completed_at = now
            abandoned += 1
            logger.warning(
                "abandoned orphan AgentRun id=%s role=%s started=%s",
                run.id,
                run.agent_role,
                run.started_at,
            )
        if abandoned:
            await db.commit()
    return {"abandoned_runs": abandoned, "cutoff": cutoff.isoformat()}


async def reconcile_expired_chapter_tasks(redis=None) -> dict:
    """Requeue chapter tasks whose lease expired (or missing) while status=running."""
    requeued = 0
    completed = 0
    failed = 0
    now = datetime.now(timezone.utc)
    async with async_session_factory() as db:
        result = await db.execute(
            select(ChapterTask).where(ChapterTask.status.in_(["running", "queued", ChapterState.QUEUED.value]))
        )
        tasks = list(result.scalars().all())
        for task in tasks:
            if task.status == "running":
                expired = (not task.lease_expires_at) or (task.lease_expires_at < now)
                if not expired:
                    continue
            elif task.status in ("queued", ChapterState.QUEUED.value):
                # leave queued tasks alone unless redis re-enqueue requested
                continue
            else:
                continue

            chapter = (
                await db.execute(
                    select(Chapter).where(
                        Chapter.book_id == task.book_id,
                        Chapter.chapter_no == task.chapter_no,
                    )
                )
            ).scalar_one_or_none()

            if chapter and chapter.status == ChapterState.FINALIZED.value:
                task.status = "completed"
                task.lease_owner = None
                task.lease_expires_at = None
                completed += 1
                continue

            if chapter:
                task.status = ChapterState.QUEUED.value
                task.lease_owner = None
                task.lease_expires_at = None
                requeued += 1
                if redis is not None:
                    try:
                        await redis.enqueue_job(
                            "run_chapter_pipeline",
                            str(chapter.id),
                            str(task.book_id),
                            task.chapter_no,
                        )
                    except Exception as e:
                        logger.warning("re-enqueue failed chapter=%s: %s", task.chapter_no, e)
            else:
                task.status = "failed"
                failed += 1
        await db.commit()
    return {"requeued": requeued, "completed": completed, "failed": failed}


async def run_reconciler(redis=None) -> dict:
    r1 = await reconcile_orphan_agent_runs()
    r2 = await reconcile_expired_chapter_tasks(redis=redis)
    out = {**r1, **r2}
    logger.info("reconciler done: %s", out)
    return out
