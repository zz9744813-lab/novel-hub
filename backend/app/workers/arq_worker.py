"""ARQ Worker - max_jobs=1, executes chapter pipeline.
Per §7.2 v7.3: 13-step fixed flow.
FIX P0-7: 断点恢复传 chapter_id 而非 task.id
"""
import asyncio
import os
import uuid
import logging

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
from sqlalchemy import select, update

logger = logging.getLogger("novelforge.worker")


async def check_resources() -> tuple[bool, dict]:
    try:
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
        safe = avail_mb > 350 and swap_pct < 60 and disk_pct < 85
        return safe, {"available_mb": avail_mb, "swap_pct": swap_pct, "disk_pct": disk_pct}
    except Exception:
        return True, {}


async def run_chapter_pipeline(ctx, chapter_id: str, book_id: str, chapter_no: int):
    """Main pipeline: 13 steps per §7.2."""
    safe, metrics = await check_resources()
    if not safe:
        logger.warning(f"Resource blocked: {metrics}")
        async with async_session_factory() as db:
            await db.execute(
                update(Chapter).where(Chapter.id == uuid.UUID(chapter_id))
                .values(status=ChapterState.RESOURCE_BLOCKED.value)
            )
            await db.commit()
        return

    logger.info(f"Starting chapter {chapter_no} pipeline (chapter_id={chapter_id})")
    await execute_pipeline(uuid.UUID(book_id), uuid.UUID(chapter_id), chapter_no)


async def on_startup(ctx):
    logger.info("NovelForge worker started")
    # Recover stale tasks — FIX P0-7: look up the correct chapter_id
    async with async_session_factory() as db:
        result = await db.execute(
            select(ChapterTask).where(
                ChapterTask.status.in_([ChapterState.QUEUED.value, "running"])
            )
        )
        tasks = result.scalars().all()
        for task in tasks:
            logger.info(f"Recovering task {task.id} for chapter {task.chapter_no}")
            task.status = ChapterState.QUEUED.value
            # FIX P0-7: Find the actual Chapter row by book_id + chapter_no
            chapter_result = await db.execute(
                select(Chapter).where(
                    Chapter.book_id == task.book_id,
                    Chapter.chapter_no == task.chapter_no,
                )
            )
            chapter = chapter_result.scalar_one_or_none()
            if chapter:
                await ctx["redis"].enqueue_job(
                    "run_chapter_pipeline",
                    str(chapter.id),  # Pass the actual Chapter.id, NOT task.id
                    str(task.book_id),
                    task.chapter_no,
                )
            else:
                logger.warning(f"Chapter not found for task {task.id} chapter {task.chapter_no}")
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
