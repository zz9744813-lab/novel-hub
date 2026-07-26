"""ARQ Worker - max_jobs=1, chapter pipeline with lease/heartbeat (P0-04)."""
from __future__ import annotations

import asyncio
import os
import uuid
import logging
import hashlib
import socket
from datetime import datetime, timezone, timedelta

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
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def advisory_lock_key(book_id: str, chapter_no: int) -> int:
    h = hashlib.sha256(f"{book_id}:{chapter_no}".encode()).digest()
    return int.from_bytes(h[:8], "big") % (2**63 - 1)


async def check_resources() -> tuple[bool, dict]:
    metrics = {}
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
        metrics.update({"available_mb": avail_mb, "swap_pct": swap_pct, "disk_pct": disk_pct})

        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    text("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
                )
                pg_active = result.scalar()
                metrics["pg_active_connections"] = pg_active
                if pg_active and pg_active > 20:
                    return False, metrics
        except Exception as e:
            logger.debug(f"Could not check PG connections: {e}")

        return avail_mb > 350 and swap_pct < 60 and disk_pct < 85, metrics
    except Exception:
        return True, {}


async def _heartbeat_loop(task_id: uuid.UUID, stop: asyncio.Event):
    while not stop.is_set():
        try:
            async with async_session_factory() as db:
                now = datetime.now(timezone.utc)
                await db.execute(
                    update(ChapterTask)
                    .where(ChapterTask.id == task_id)
                    .values(
                        heartbeat_at=now,
                        lease_expires_at=now + timedelta(seconds=90),
                    )
                )
                await db.commit()
        except Exception as e:
            logger.warning(f"heartbeat failed: {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass


async def run_chapter_pipeline(ctx, chapter_id: str, book_id: str, chapter_no: int):
    safe, metrics = await check_resources()
    if not safe:
        logger.warning(f"Resource blocked: {metrics}")
        async with async_session_factory() as db:
            await db.execute(
                update(Chapter)
                .where(Chapter.id == uuid.UUID(chapter_id))
                .values(status=ChapterState.RESOURCE_BLOCKED.value)
            )
            await db.commit()
        return

    # Readiness: worker refuses jobs if provider not ready
    if os.environ.get("WORKER_READY", "1") != "1":
        logger.error("Worker not ready (provider/bindings); skipping job")
        return

    lock_key = advisory_lock_key(book_id, int(chapter_no))
    task_row_id = None
    stop = asyncio.Event()
    hb_task = None

    try:
        async with async_session_factory() as db:
            # Advisory lock for chapter uniqueness
            got = (
                await db.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": lock_key},
                )
            ).scalar()
            if not got:
                logger.warning(f"Could not acquire advisory lock for ch {chapter_no}")
                return

            # Find/create chapter task lease (latest row — API may create duplicates)
            result = await db.execute(
                select(ChapterTask)
                .where(
                    ChapterTask.book_id == uuid.UUID(book_id),
                    ChapterTask.chapter_no == int(chapter_no),
                )
                .order_by(ChapterTask.created_at.desc())
                .limit(1)
            )
            task = result.scalar_one_or_none()
            now = datetime.now(timezone.utc)
            if task is None:
                task = ChapterTask(
                    id=uuid.uuid4(),
                    book_id=uuid.UUID(book_id),
                    chapter_no=int(chapter_no),
                    status="running",
                )
                db.add(task)
                await db.flush()
            else:
                # Collapse older duplicate task rows for same book/chapter
                await db.execute(
                    update(ChapterTask)
                    .where(
                        ChapterTask.book_id == uuid.UUID(book_id),
                        ChapterTask.chapter_no == int(chapter_no),
                        ChapterTask.id != task.id,
                    )
                    .values(status="superseded", lease_owner=None, lease_expires_at=None)
                )

            # Skip if lease held by another live worker
            if (
                task.lease_owner
                and task.lease_owner != WORKER_ID
                and task.lease_expires_at
                and task.lease_expires_at > now
            ):
                logger.info(f"Lease held by {task.lease_owner}, skip")
                await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
                await db.commit()
                return

            # Skip if chapter already finalized / needs human / not runnable
            ch = (
                await db.execute(select(Chapter).where(Chapter.id == uuid.UUID(chapter_id)))
            ).scalar_one_or_none()
            if ch:
                if ch.status == ChapterState.FINALIZED.value and ch.finalized_version:
                    task.status = "completed"
                    await db.commit()
                    await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
                    return
                if ch.status in {
                    ChapterState.NEEDS_HUMAN.value,
                    "needs_human",
                    "running",
                    "drafting",
                    "planning",
                    "reviewing",
                    "patching",
                    "finalizing",
                    "state_extracting",
                    "consistency_check",
                }:
                    # Only re-enter from queued/failed/resource_blocked
                    logger.info(
                        f"Skip chapter {chapter_no}: status={ch.status} not re-runnable"
                    )
                    await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
                    await db.commit()
                    return
                if ch.status not in {
                    ChapterState.QUEUED.value,
                    ChapterState.FAILED.value,
                    "queued",
                    "failed",
                    "resource_blocked",
                    ChapterState.RESOURCE_BLOCKED.value if hasattr(ChapterState, "RESOURCE_BLOCKED") else "resource_blocked",
                }:
                    logger.info(f"Skip chapter {chapter_no}: unexpected status={ch.status}")
                    await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
                    await db.commit()
                    return

            task.status = "running"
            task.lease_owner = WORKER_ID
            task.lease_expires_at = now + timedelta(seconds=90)
            task.heartbeat_at = now
            task.attempt_no = (task.attempt_no or 0) + 1
            task.last_error_code = None
            task.last_error_detail = None
            task_row_id = task.id
            await db.commit()

        hb_task = asyncio.create_task(_heartbeat_loop(task_row_id, stop))

        logger.info(f"Starting chapter {chapter_no} pipeline (chapter_id={chapter_id})")
        await execute_pipeline(uuid.UUID(book_id), uuid.UUID(chapter_id), chapter_no)

        async with async_session_factory() as db:
            await db.execute(
                update(ChapterTask)
                .where(ChapterTask.id == task_row_id)
                .values(status="completed", lease_owner=None, lease_expires_at=None)
            )
            await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
            await db.commit()

    except Exception as e:
        logger.error(f"Pipeline failed for chapter {chapter_no}: {e}", exc_info=True)
        try:
            async with async_session_factory() as db:
                await db.execute(
                    update(Chapter)
                    .where(Chapter.id == uuid.UUID(chapter_id))
                    .values(status=ChapterState.FAILED.value)
                )
                if task_row_id:
                    await db.execute(
                        update(ChapterTask)
                        .where(ChapterTask.id == task_row_id)
                        .values(
                            status="failed",
                            last_error_code="pipeline_error",
                            last_error_detail=str(e)[:2000],
                            lease_owner=None,
                            lease_expires_at=None,
                        )
                    )
                await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": lock_key})
                await db.commit()
        except Exception as mark_err:
            logger.error(f"Failed to mark chapter as failed: {mark_err}")
    finally:
        stop.set()
        if hb_task:
            try:
                await hb_task
            except Exception:
                pass


async def on_startup(ctx):
    logger.info("NovelForge worker started")
    # Provider readiness
    try:
        from app.startup_checks import check_provider_ready, check_required_bindings

        ok, msg = await check_provider_ready()
        if not ok:
            os.environ["WORKER_READY"] = "0"
            logger.error(f"Provider not ready: {msg}")
        else:
            bok, bmsg = await check_required_bindings()
            if not bok:
                os.environ["WORKER_READY"] = "0"
                logger.error(f"Bindings not ready: {bmsg}")
            else:
                os.environ["WORKER_READY"] = "1"
                logger.info("Worker readiness OK")
    except Exception as e:
        os.environ["WORKER_READY"] = "0"
        logger.error(f"Readiness check failed: {e}")

    # Recover only expired leases
    async with async_session_factory() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(ChapterTask).where(
                ChapterTask.status.in_([ChapterState.QUEUED.value, "running", "queued"])
            )
        )
        tasks = result.scalars().all()
        for task in tasks:
            expired = (not task.lease_expires_at) or (task.lease_expires_at < now)
            if task.status == "running" and not expired:
                continue
            chapter_result = await db.execute(
                select(Chapter).where(
                    Chapter.book_id == task.book_id,
                    Chapter.chapter_no == task.chapter_no,
                )
            )
            chapter = chapter_result.scalar_one_or_none()
            if chapter and chapter.status == ChapterState.FINALIZED.value:
                task.status = "completed"
                continue
            if chapter:
                logger.info(
                    f"Recovering expired lease: chapter {task.chapter_no}, chapter_id={chapter.id}"
                )
                task.status = ChapterState.QUEUED.value
                task.lease_owner = None
                task.lease_expires_at = None
                await ctx["redis"].enqueue_job(
                    "run_chapter_pipeline",
                    str(chapter.id),
                    str(task.book_id),
                    task.chapter_no,
                )
            else:
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
    job_timeout = int(os.environ.get("ARQ_JOB_TIMEOUT", "14400"))
    max_tries = 1
