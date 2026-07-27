"""Transactional outbox dispatcher (AI__.md v3.0 §7.2 / B-11).

Polls chapter_dispatch_outbox and enqueues ARQ jobs. Safe to call from
worker startup and a background loop.
"""
from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, text, update

from app.database import async_session_factory
from app.models import ChapterDispatchOutbox, ChapterRun

logger = logging.getLogger("novelforge.outbox")
DISPATCHER_ID = f"{socket.gethostname()}:{os.getpid()}"
MAX_ATTEMPTS = int(os.environ.get("OUTBOX_MAX_ATTEMPTS", "20"))


async def reclaim_stale_dispatching(db, older_than_sec: int = 60) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_sec)
    result = await db.execute(
        update(ChapterDispatchOutbox)
        .where(
            ChapterDispatchOutbox.status == "dispatching",
            ChapterDispatchOutbox.locked_at.is_not(None),
            ChapterDispatchOutbox.locked_at < cutoff,
        )
        .values(status="pending", locked_at=None, locked_by=None)
    )
    return result.rowcount or 0


async def claim_pending_batch(db, limit: int = 10) -> list[ChapterDispatchOutbox]:
    """Claim pending outbox rows with FOR UPDATE SKIP LOCKED."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ChapterDispatchOutbox)
        .where(
            ChapterDispatchOutbox.status == "pending",
            ChapterDispatchOutbox.available_at <= now,
        )
        .order_by(ChapterDispatchOutbox.available_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    rows = list(result.scalars().all())
    for row in rows:
        row.status = "dispatching"
        row.locked_at = now
        row.locked_by = DISPATCHER_ID
        row.attempts = int(row.attempts or 0) + 1
    return rows


async def enqueue_arq(payload: dict) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings
    import redis.asyncio.connection as _rc

    _rc.AbstractConnection.lib_name = None
    _rc.AbstractConnection.lib_version = None
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    redis_parts = redis_url.replace("redis://", "").split(":")
    r_host = redis_parts[0]
    r_port = int(redis_parts[1].split("/")[0]) if len(redis_parts) > 1 else 6379
    pool = await create_pool(RedisSettings(host=r_host, port=r_port))
    try:
        chapter_id = payload["chapter_id"]
        book_id = payload["book_id"]
        chapter_no = int(payload["chapter_no"])
        job_id = payload.get("job_id") or f"chapter-run:{payload.get('run_id')}:{payload.get('dedupe_key')}"
        await pool.enqueue_job(
            "run_chapter_pipeline",
            chapter_id,
            book_id,
            chapter_no,
            _job_id=job_id,
        )
    finally:
        await pool.close()


async def dispatch_once(limit: int = 10) -> dict:
    reclaimed = 0
    dispatched = 0
    failed = 0
    dead = 0
    async with async_session_factory() as db:
        reclaimed = await reclaim_stale_dispatching(db)
        rows = await claim_pending_batch(db, limit=limit)
        await db.commit()

    for row_id in []:  # placeholder to keep structure
        pass

    # re-load claimed ids in short sessions per row to avoid long locks during Redis
    async with async_session_factory() as db:
        result = await db.execute(
            select(ChapterDispatchOutbox).where(
                ChapterDispatchOutbox.status == "dispatching",
                ChapterDispatchOutbox.locked_by == DISPATCHER_ID,
            )
        )
        rows = list(result.scalars().all())
        await db.commit()

    for row in rows:
        async with async_session_factory() as db:
            fresh = (
                await db.execute(
                    select(ChapterDispatchOutbox).where(ChapterDispatchOutbox.id == row.id)
                )
            ).scalar_one_or_none()
            if not fresh or fresh.status != "dispatching":
                continue
            try:
                payload = dict(fresh.payload or {})
                payload.setdefault("run_id", str(fresh.chapter_run_id))
                payload.setdefault("dedupe_key", fresh.dedupe_key)
                await enqueue_arq(payload)
                fresh.status = "dispatched"
                fresh.dispatched_at = datetime.now(timezone.utc)
                fresh.locked_at = None
                fresh.locked_by = None
                fresh.last_error = None
                dispatched += 1
            except Exception as e:
                logger.warning("outbox dispatch failed id=%s: %s", fresh.id, e)
                fresh.last_error = str(e)[:1000]
                fresh.locked_at = None
                fresh.locked_by = None
                if int(fresh.attempts or 0) >= MAX_ATTEMPTS:
                    fresh.status = "dead"
                    dead += 1
                else:
                    # exponential backoff capped at 5 min
                    delay = min(300, 2 ** min(int(fresh.attempts or 1), 8))
                    fresh.status = "pending"
                    fresh.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    failed += 1
            await db.commit()

    # Backfill: queued runs without pending/dispatching outbox
    backfilled = await ensure_outbox_for_orphan_runs()
    return {
        "reclaimed": reclaimed,
        "dispatched": dispatched,
        "failed": failed,
        "dead": dead,
        "backfilled": backfilled,
    }


async def ensure_outbox_for_orphan_runs() -> int:
    """INV-11 / B-11: API wrote Run but Redis enqueue failed."""
    added = 0
    active = ("queued", "retryable")
    async with async_session_factory() as db:
        runs = (
            await db.execute(
                select(ChapterRun).where(
                    ChapterRun.status.in_(active),
                    ChapterRun.lease_owner.is_(None),
                )
            )
        ).scalars().all()
        for run in runs:
            existing = (
                await db.execute(
                    select(ChapterDispatchOutbox).where(
                        ChapterDispatchOutbox.chapter_run_id == run.id,
                        ChapterDispatchOutbox.status.in_(("pending", "dispatching")),
                    )
                )
            ).scalar_one_or_none()
            if existing:
                continue
            dedupe = f"dispatch:{run.id}:{uuid.uuid4().hex[:8]}"
            db.add(
                ChapterDispatchOutbox(
                    id=uuid.uuid4(),
                    chapter_run_id=run.id,
                    dedupe_key=dedupe,
                    event_type="dispatch_chapter_run",
                    payload={
                        "chapter_id": str(run.chapter_id),
                        "book_id": str(run.book_id),
                        "chapter_no": run.chapter_no,
                        "run_id": str(run.id),
                        "job_id": f"chapter-run:{run.id}:{dedupe}",
                    },
                    status="pending",
                    available_at=datetime.now(timezone.utc),
                )
            )
            added += 1
        if added:
            await db.commit()
    return added


async def create_run_and_outbox(
    db,
    *,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    chapter_no: int,
    outline_version_id: uuid.UUID,
    request_id: str,
    created_by: str = "api",
    resume_from_run_id: uuid.UUID | None = None,
    model_binding_snapshot: dict | None = None,
    budget_snapshot: dict | None = None,
) -> ChapterRun:
    """Create ChapterRun + pending Outbox in the caller's transaction."""
    run = ChapterRun(
        id=uuid.uuid4(),
        book_id=book_id,
        chapter_id=chapter_id,
        chapter_no=chapter_no,
        outline_version_id=outline_version_id,
        pipeline_version="pipeline-v2",
        status="queued",
        control_requested="none",
        request_id=request_id,
        resume_from_run_id=resume_from_run_id,
        model_binding_snapshot=model_binding_snapshot or {},
        budget_snapshot=budget_snapshot or {},
        created_by=created_by,
    )
    db.add(run)
    await db.flush()
    dedupe = f"dispatch:{run.id}:initial"
    db.add(
        ChapterDispatchOutbox(
            id=uuid.uuid4(),
            chapter_run_id=run.id,
            dedupe_key=dedupe,
            event_type="dispatch_chapter_run",
            payload={
                "chapter_id": str(chapter_id),
                "book_id": str(book_id),
                "chapter_no": chapter_no,
                "run_id": str(run.id),
                "job_id": f"chapter-run:{run.id}:{dedupe}",
            },
            status="pending",
            available_at=datetime.now(timezone.utc),
        )
    )
    return run
