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
from arq import cron
from app.database import async_session_factory
from app.state_machine import ChapterState
from app.models import Chapter, ChapterTask, ChapterRun
from app.engine.pipeline import execute_pipeline
from app.engine.outcomes import PipelineOutcome, PipelineResult
from app.engine.step_runner import acquire_run_lease, release_run_lease
from app.workers.writing_session_jobs import advance_writing_session_job
from app.model_autopilot.autoconfig_job import run_model_autoconfigure_job, run_model_detection_job
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


async def _heartbeat_loop(task_id: uuid.UUID, stop: asyncio.Event, run_id: uuid.UUID | None = None):
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
                if run_id:
                    await db.execute(
                        update(ChapterRun)
                        .where(
                            ChapterRun.id == run_id,
                            ChapterRun.lease_owner == WORKER_ID,
                        )
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


async def _insert_session_advance_outbox(db, session_id: uuid.UUID, run_id: uuid.UUID) -> None:
    """Idempotent, same-txn session advance record (v9.4 spec §12/§13)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models import SessionAdvanceOutbox

    await db.execute(
        pg_insert(SessionAdvanceOutbox)
        .values(
            id=uuid.uuid4(),
            writing_session_id=session_id,
            completed_run_id=run_id,
            event_type="advance_writing_session",
            dedupe_key=f"session-advance:{session_id}:{run_id}",
            payload={"session_id": str(session_id), "completed_run_id": str(run_id)},
            status="pending",
            available_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )


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
    chapter_run_id = None
    stop = asyncio.Event()
    hb_task = None
    advisory_held = False

    try:
        # Resolve ChapterRun first (B-03: prefer DB lease CAS over session advisory)
        async with async_session_factory() as db:
            ch0 = (
                await db.execute(select(Chapter).where(Chapter.id == uuid.UUID(chapter_id)))
            ).scalar_one_or_none()
            if ch0 and getattr(ch0, "active_run_id", None):
                chapter_run_id = ch0.active_run_id
            else:
                run0 = (
                    await db.execute(
                        select(ChapterRun)
                        .where(ChapterRun.chapter_id == uuid.UUID(chapter_id))
                        .order_by(ChapterRun.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if run0:
                    chapter_run_id = run0.id

        if chapter_run_id:
            leased = await acquire_run_lease(chapter_run_id, WORKER_ID)
            if not leased:
                logger.info(f"Could not acquire chapter_run lease for ch {chapter_no}")
                return
        else:
            # Legacy path: short advisory only for task lease setup (not held across LLM)
            async with async_session_factory() as db:
                got = (
                    await db.execute(
                        text("SELECT pg_try_advisory_lock(:k)"),
                        {"k": lock_key},
                    )
                ).scalar()
                if not got:
                    logger.warning(f"Could not acquire advisory lock for ch {chapter_no}")
                    return
                pass  # advisory no longer held across LLM (B-03)
                await db.commit()

        async with async_session_factory() as db:
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
                pass  # advisory no longer held across LLM (B-03)
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
                    pass  # advisory no longer held across LLM (B-03)
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
                    pass  # advisory no longer held across LLM (B-03)
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
                    pass  # advisory no longer held across LLM (B-03)
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

        hb_task = asyncio.create_task(_heartbeat_loop(task_row_id, stop, chapter_run_id))

        logger.info(
            f"Starting chapter {chapter_no} pipeline (chapter_id={chapter_id} run_id={chapter_run_id})"
        )
        result = await execute_pipeline(
            uuid.UUID(book_id),
            uuid.UUID(chapter_id),
            chapter_no,
            chapter_run_id=chapter_run_id,
        )
        if not isinstance(result, PipelineResult):
            result = PipelineResult(
                outcome=PipelineOutcome.PERMANENT_FAILURE,
                chapter_id=uuid.UUID(chapter_id),
                error_code="missing_pipeline_result",
            )

        task_status = "failed"
        run_status = "failed"
        now = datetime.now(timezone.utc)
        match result.outcome:
            case PipelineOutcome.FINALIZED:
                # INV-05: only succeed after final pointer check
                async with async_session_factory() as db:
                    ch = (
                        await db.execute(
                            select(Chapter).where(Chapter.id == uuid.UUID(chapter_id))
                        )
                    ).scalar_one_or_none()
                    if (
                        ch
                        and ch.status == ChapterState.FINALIZED.value
                        and ch.finalized_version is not None
                    ):
                        task_status = "completed"
                        run_status = "succeeded"
                    else:
                        task_status = "failed"
                        run_status = "failed"
                        result = PipelineResult(
                            outcome=PipelineOutcome.PERMANENT_FAILURE,
                            chapter_id=uuid.UUID(chapter_id),
                            chapter_run_id=chapter_run_id,
                            error_code="final_pointer_missing",
                            detail={"chapter_status": getattr(ch, "status", None)},
                        )
            case PipelineOutcome.BLOCKED_DEPENDENCY:
                task_status = "waiting_dependency"
                run_status = "waiting_dependency"
            case PipelineOutcome.RESOURCE_BLOCKED:
                task_status = "resource_blocked"
                run_status = "retryable"
            case PipelineOutcome.PAUSED:
                task_status = "paused"
                run_status = "paused"
            case PipelineOutcome.NEEDS_HUMAN:
                task_status = "needs_human"
                run_status = "needs_human"
            case PipelineOutcome.RETRYABLE_FAILURE:
                task_status = "failed"
                run_status = "retryable"
            case PipelineOutcome.PERMANENT_FAILURE:
                task_status = "failed"
                run_status = "failed"

        async with async_session_factory() as db:
            await db.execute(
                update(ChapterTask)
                .where(ChapterTask.id == task_row_id)
                .values(
                    status=task_status,
                    lease_owner=None,
                    lease_expires_at=None,
                    last_error_code=result.error_code,
                    last_error_detail=(str(result.detail)[:2000] if result.detail else None),
                )
            )
            if chapter_run_id:
                # v9.4: lock the run row and, in the SAME txn as status=succeeded,
                # insert the session advance outbox (spec §12). No finalizer-side
                # enqueue; the outbox is the only path that advances the session.
                run_row = (
                    await db.execute(
                        select(ChapterRun).where(ChapterRun.id == chapter_run_id).with_for_update()
                    )
                ).scalar_one_or_none()
                if run_row is not None:
                    run_row.status = run_status
                    run_row.lease_owner = None
                    run_row.lease_expires_at = None
                    run_row.finished_at = (
                        now if run_status in ("succeeded", "failed", "needs_human", "cancelled") else None
                    )
                    run_row.error_code = result.error_code
                    run_row.error_detail = result.detail or None
                    if run_status == "succeeded" and run_row.writing_session_id:
                        await _insert_session_advance_outbox(
                            db, run_row.writing_session_id, run_row.id
                        )
            pass  # advisory no longer held across LLM (B-03)
            await db.commit()
        logger.info(
            "chapter %s pipeline outcome=%s task=%s run=%s",
            chapter_no,
            result.outcome.value,
            task_status,
            run_status,
        )
        try:
            from app.events import publish_event
            await publish_event(
                "chapter_run.updated",
                {
                    "book_id": book_id,
                    "chapter_id": chapter_id,
                    "chapter_no": chapter_no,
                    "run_id": chapter_run_id,
                    "run_status": run_status,
                    "task_status": task_status,
                    "error_code": result.error_code,
                    "current_step": getattr(result, "current_step", None),
                },
            )
        except Exception:
            logger.debug("chapter event publish failed", exc_info=True)

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
                pass  # advisory no longer held across LLM (B-03)
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
        if chapter_run_id:
            try:
                await release_run_lease(chapter_run_id, WORKER_ID)
            except Exception as e:
                logger.warning(f"release_run_lease failed: {e}")


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

    # P1 CORE-005: abandon stale AgentRuns + recover expired leases
    try:
        from app.engine.reconciler import run_reconciler

        report = await run_reconciler(redis=ctx.get("redis"))
        logger.info(f"Startup reconciler: {report}")
    except Exception as e:
        logger.error(f"Startup reconciler failed: {e}")

    # B-11: drain transactional outbox
    try:
        from app.workers.outbox_dispatcher import dispatch_once

        out = await dispatch_once()
        logger.info(f"Startup outbox dispatch: {out}")
    except Exception as e:
        logger.error(f"Startup outbox dispatch failed: {e}")

    # Recover only expired leases (legacy path kept as safety net)
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
            if chapter and task.status == "running" and expired:
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
            elif not chapter and task.status == "running":
                task.status = "failed"
        await db.commit()


async def on_shutdown(ctx):
    logger.info("NovelForge worker shutting down")


_redis_host = os.environ.get("REDIS_HOST", "redis")
_redis_port = int(os.environ.get("REDIS_PORT", "6379"))




async def outbox_tick(ctx):
    """Periodic outbox drain (every ~minute via cron)."""
    try:
        from app.workers.outbox_dispatcher import dispatch_once
        report = await dispatch_once()
        if report.get("dispatched") or report.get("backfilled") or report.get("failed"):
            logger.info(f"outbox_tick: {report}")
    except Exception as e:
        logger.warning(f"outbox_tick failed: {e}")


async def session_outbox_tick(ctx):
    """v9.4: drain session-advance outbox (spec §13)."""
    try:
        from app.workers.session_outbox_dispatcher import dispatch_session_outbox_once

        report = await dispatch_session_outbox_once()
        if report.get("dispatched") or report.get("failed"):
            logger.info(f"session_outbox_tick: {report}")
    except Exception as e:
        logger.warning(f"session_outbox_tick failed: {e}")


async def session_reconciler_cron(ctx):
    """v9.4: session reconciler (spec §32)."""
    from app.workers.writing_session_jobs import session_reconciler_tick

    return await session_reconciler_tick(ctx)


async def model_catalog_sync_cron(ctx):
    """v9.5: provider /models → catalog (spec §25)."""
    from app.model_autopilot.jobs import model_catalog_sync_tick

    return await model_catalog_sync_tick(ctx)


async def model_health_probe_cron(ctx):
    """v9.5: periodic health probing (spec §25–§28)."""
    from app.model_autopilot.jobs import model_health_probe_tick

    return await model_health_probe_tick(ctx)


async def run_import_pipeline_job(ctx, session_id: str):
    """v8 multi-agent import analysis (checkpointed). Shares max_jobs=1 with chapter pipeline."""
    logger.info("import_pipeline start session=%s", session_id)
    try:
        from app.engine.import_pipeline import run_import_pipeline

        report = await run_import_pipeline(session_id)
        logger.info("import_pipeline done session=%s report=%s", session_id, report)
        return report
    except Exception as e:
        logger.exception("import_pipeline failed session=%s: %s", session_id, e)
        try:
            from app.database import async_session_factory
            from app.models.tables import ImportSession
            from sqlalchemy import select
            from datetime import datetime, timezone

            async with async_session_factory() as db:
                sess = (
                    await db.execute(
                        select(ImportSession).where(ImportSession.id == uuid.UUID(session_id))
                    )
                ).scalar_one_or_none()
                if sess and sess.status not in ("completed", "preview_ready", "needs_human"):
                    sess.status = "failed"
                    sess.error_code = "PIPELINE_FAILED"
                    sess.error_detail = str(e)[:2000]
                    sess.updated_at = datetime.now(timezone.utc)
                    await db.commit()
        except Exception as e2:
            logger.warning("failed to mark import session failed: %s", e2)
        raise


async def run_research_task_job(ctx, task_id: str):
    """v9.1 research scraping task (spec §20). Shares max_jobs=1."""
    from app.workers.research_jobs import run_research_task

    return await run_research_task(ctx, task_id)


async def run_editorial_revision_job(ctx, review_id: str, level: str | None = None):
    """v9.3 ELL revision ladder L0..L5 (spec §87, §29)."""
    from app.editorial.jobs import run_editorial_revision_job as _job

    return await _job(ctx, review_id, level)


async def analyze_editorial_review_job(ctx, review_id: str):
    """v9.3 ELL deterministic feedback analysis (spec §42)."""
    from app.editorial.jobs import analyze_editorial_review_job as _job

    return await _job(ctx, review_id)


class WorkerSettings:
    # arq cron: minute-level outbox drain (B-11) + v9.4 session outbox/reconciler
    # + v9.5 model autopilot (catalog every 30min, probes every 5min)
    cron_jobs = [
        cron(outbox_tick, second={0, 30}),
        cron(session_outbox_tick, second={10, 40}),
        cron(session_reconciler_cron, second={20, 50}),
        cron(model_catalog_sync_cron, minute={0}),
        cron(model_health_probe_cron, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
    ]

    functions = [
        run_chapter_pipeline,
        outbox_tick,
        session_outbox_tick,
        session_reconciler_cron,
        model_catalog_sync_cron,
        model_health_probe_cron,
        run_import_pipeline_job,
        run_research_task_job,
        run_editorial_revision_job,
        analyze_editorial_review_job,
        advance_writing_session_job,
        run_model_detection_job,
        run_model_autoconfigure_job,
    ]
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
