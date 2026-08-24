"""v9.4: transactional outbox dispatcher for session advancement (spec §13).

Mirrors the chapter-dispatch outbox discipline: claim via FOR UPDATE SKIP LOCKED,
best-effort Redis enqueue, backoff with stale reclaim and dead state. The only
way a WritingSession moves is DB → outbox → dispatcher → ARQ
(advance_writing_session).
"""
from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, update

from app.database import async_session_factory
from app.models import SessionAdvanceOutbox

logger = logging.getLogger("novelforge.session_outbox")
DISPATCHER_ID = f"{socket.gethostname()}:{os.getpid()}"
MAX_ATTEMPTS = int(os.environ.get("SESSION_OUTBOX_MAX_ATTEMPTS", "20"))


async def reclaim_stale_session_dispatch(db, older_than_sec: int = 60) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_sec)
    result = await db.execute(
        update(SessionAdvanceOutbox)
        .where(
            SessionAdvanceOutbox.status == "dispatching",
            SessionAdvanceOutbox.locked_at.is_not(None),
            SessionAdvanceOutbox.locked_at < cutoff,
        )
        .values(status="pending", locked_at=None, locked_by=None)
    )
    return result.rowcount or 0


async def claim_pending_session_batch(db, limit: int = 10) -> list[SessionAdvanceOutbox]:
    """Claim pending session-advance rows with FOR UPDATE SKIP LOCKED."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(SessionAdvanceOutbox)
        .where(
            SessionAdvanceOutbox.status == "pending",
            SessionAdvanceOutbox.available_at <= now,
        )
        .order_by(SessionAdvanceOutbox.available_at)
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


async def enqueue_advance_arq(session_id: uuid.UUID, run_id: uuid.UUID | None = None) -> str:
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
        job_id = f"session-advance:{session_id}:{run_id or 'none'}"
        await pool.enqueue_job(
            "advance_writing_session",
            str(session_id),
            str(run_id) if run_id else "",
            _job_id=job_id,
        )
        return job_id
    finally:
        await pool.close()


async def dispatch_session_outbox_once(limit: int = 10) -> dict:
    reclaimed = dispatched = failed = dead = 0
    async with async_session_factory() as db:
        reclaimed = await reclaim_stale_session_dispatch(db)
        rows = await claim_pending_session_batch(db, limit=limit)
        await db.commit()

    for row in rows:
        async with async_session_factory() as db:
            fresh = (
                await db.execute(
                    select(SessionAdvanceOutbox).where(SessionAdvanceOutbox.id == row.id)
                )
            ).scalar_one_or_none()
            if not fresh or fresh.status != "dispatching":
                continue
            try:
                await enqueue_advance_arq(fresh.writing_session_id, fresh.completed_run_id)
                fresh.status = "dispatched"
                fresh.dispatched_at = datetime.now(timezone.utc)
                fresh.locked_at = None
                fresh.locked_by = None
                fresh.last_error = None
                dispatched += 1
            except Exception as e:
                logger.warning("session outbox dispatch failed id=%s: %s", fresh.id, e)
                fresh.last_error = str(e)[:1000]
                fresh.locked_at = None
                fresh.locked_by = None
                if int(fresh.attempts or 0) >= MAX_ATTEMPTS:
                    fresh.status = "dead"
                    dead += 1
                else:
                    delay = min(300, 2 ** min(int(fresh.attempts or 1), 8))
                    fresh.status = "pending"
                    fresh.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
                    failed += 1
            await db.commit()

    return {
        "reclaimed": reclaimed,
        "dispatched": dispatched,
        "failed": failed,
        "dead": dead,
    }
