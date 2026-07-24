"""ARQ Worker - max_jobs=1, executes chapter pipeline.
Per §7.2 v7.3: 13-step fixed flow.
Per C-20: Resource check includes PG active connections.
"""
import asyncio
import os
import uuid
import logging

# Monkey-patch redis to disable CLIENT SETINFO before importing arq
import redis.asyncio.connection as _redis_conn
_orig_on_connect = _redis_conn.AbstractConnection.on_connect

async def _patched_on_connect(self, *args, **kwargs):
    self.lib_name = None
    self.lib_version = None
    return await _orig_on_connect(self, *args, **kwargs)

_redis_conn.AbstractConnection.on_connect = _patched_on_connect

from arq.connections import RedisSettings
from app.database import async_session_factory
from app.state_machine import ChapterState
from app.models import Chapter, ChapterTask
from app.engine.pipeline import execute_pipeline
from sqlalchemy import select, update, text

logger = logging.getLogger("novelforge.worker")


async def check_resources() -> tuple[bool, dict]:
    """C-20: Resource red-line check.
    Checks: available memory < 350MB, swap > 60%, disk > 85%, PG connections > 20.
    """
    metrics = {}
    try:
        # Memory check
        with open("/proc/meminfo") as f:
            lines = f.readlines()
        mem = {}
        for line in lines:
            parts = line.split()
            key = parts[0].rstrip(":")
            if key in ("MemAvailable", "SwapTotal", "SwapFree"):
                mem[key] = int(parts[1])
        avail_mb = mem.get("MemAvailable", 999999) // 1024
        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        swap_pct = ((swap_total - swap_free) * 100 // swap_total) if swap_total > 0 else 0
        import shutil
        disk = shutil.disk_usage("/")
        disk_pct = disk.used * 100 // disk.total
        metrics.update({
            "available_mb": avail_mb,
            "swap_pct": swap_pct,
            "disk_pct": disk_pct,
        })

        # §2.8: Check PG active connections > 20
        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    text("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
                )
                pg_active = result.scalar()
                metrics["pg_active_connections"] = pg_active
                if pg_active and pg_active > 20:
                    logger.warning(f"PG active connections too high: {pg_active}")
                    return False, metrics
        except Exception as e:
            logger.debug(f"Could not check PG connections: {e}")
            # Don't block if PG check fails - memory/disk check is primary

        safe = avail_mb > 350 and swap_pct < 60 and disk_pct < 85
        return safe, metrics
    except Exception:
        return True, {}


async def run_chapter_pipeline(ctx, chapter_id: str, book_id: str, chapter_no: int):
    """Main pipeline: 13 steps per §7.2."""
    safe, metrics = await check_resources()
    if not safe:
        logger.warning(f"Resource blocked: {metrics}")
        async with async_session_factory() as db:
            try:
                await db.execute(
                    update(Chapter).where(Chapter.id == uuid.UUID(chapter_id))
                    .values(status=ChapterState.RESOURCE_BLOCKED.value)
                )
                await db.commit()
            except Exception as e:
                logger.error(f"Failed to set resource_blocked status: {e}")
                await db.rollback()
        return

    logger.info(f"Starting chapter {chapter_no} pipeline (chapter_id={chapter_id})")
    try:
        await execute_pipeline(uuid.UUID(book_id), uuid.UUID(chapter_id), chapter_no)
    except Exception as e:
        logger.error(f"Pipeline failed for chapter {chapter_no}: {e}", exc_info=True)
        # Use a fresh session to mark the chapter as failed
        # (the pipeline's session may be in an aborted state)
        try:
            async with async_session_factory() as db:
                await db.execute(
                    update(Chapter).where(Chapter.id == uuid.UUID(chapter_id))
                    .values(status=ChapterState.FAILED.value)
                )
                await db.commit()
        except Exception as mark_err:
            logger.error(f"Failed to mark chapter as failed: {mark_err}")


async def on_startup(ctx):
    logger.info("NovelForge worker started")
    # Recover stale tasks per §2.6: "Redis restart -> scan QUEUED/RUNNING tasks"
    async with async_session_factory() as db:
        result = await db.execute(
            select(ChapterTask).where(
                ChapterTask.status.in_([ChapterState.QUEUED.value, "running"])
            )
        )
        tasks = result.scalars().all()
        for task in tasks:
            # Look up the actual Chapter for this task's book + chapter_no
            chapter_result = await db.execute(
                select(Chapter).where(
                    Chapter.book_id == task.book_id,
                    Chapter.chapter_no == task.chapter_no,
                )
            )
            chapter = chapter_result.scalar_one_or_none()
            if chapter:
                logger.info(f"Recovering task: chapter {task.chapter_no}, chapter_id={chapter.id}")
                task.status = ChapterState.QUEUED.value
                await ctx["redis"].enqueue_job("run_chapter_pipeline",
                    str(chapter.id), str(task.book_id), task.chapter_no)
            else:
                logger.warning(f"Recovery: no chapter found for task book={task.book_id} ch={task.chapter_no}")
                task.status = "failed"
        await db.commit()


async def on_shutdown(ctx):
    logger.info("NovelForge worker shutting down")


_redis_host = os.environ.get("REDIS_HOST", "redis")
_redis_port = int(os.environ.get("REDIS_PORT", "6379"))


class WorkerSettings:
    functions = [run_chapter_pipeline]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings(
        host=_redis_host,
        port=_redis_port,
        database=0,
    )
    max_jobs = int(os.environ.get("ARQ_MAX_JOBS", "1"))
    job_timeout = 600
    max_tries = 3
