"""Cross-process realtime event bus for API WebSockets and workers."""
from __future__ import annotations

import json
import os
from typing import Any

from redis.asyncio import Redis

CHANNEL = "novelforge:events"


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://redis:6379/0")


async def publish_event(event_type: str, payload: dict[str, Any]) -> None:
    """Publish one best-effort event; event delivery must never fail a job."""
    client = None
    try:
        client = Redis.from_url(_redis_url(), decode_responses=True)
        await client.publish(
            CHANNEL,
            json.dumps({"type": event_type, **payload}, ensure_ascii=False, default=str),
        )
    except Exception:
        # Realtime delivery is advisory. The database remains the source of truth.
        return
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
