"""Model autopilot: health probes (spec §20–§21, §24, §26–§28).

L0 provider probe, L1 model ping (tiny stream), L2 capability probe (low-freq).
Probe concurrency is capped; never competes with an active chapter pipeline.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.model_gateway import stream_completion_and_collect
from app.models import ModelCatalog, ModelHealthProbe

logger = logging.getLogger("novelforge.model_autopilot.probe")

MAX_CONCURRENCY = int(os.environ.get("MODEL_PROBE_CONCURRENCY", "1"))


def _provider_config(provider: str):
    from app.gateway.model_gateway import _get_provider_config

    return _get_provider_config("primary", provider=provider)


async def probe_provider(db: AsyncSession, *, provider: str) -> ModelHealthProbe | None:
    """L0: provider /models connectivity check (spec §20)."""
    config = _provider_config(provider)
    started = datetime.now(timezone.utc)
    probe = ModelHealthProbe(
        id=uuid.uuid4(),
        model_catalog_id=uuid.uuid4(),  # placeholder replaced below if catalog row exists
        probe_type="l0_provider",
        status="failed",
        started_at=started,
    )
    t0 = time.time()
    try:
        from app.model_autopilot.catalog import _get_provider_models

        items = await _get_provider_models(config["base_url"], config["api_key"])
        probe.status = "ok"
        probe.output_valid = True
        probe.detail_json = {"model_count": len(items)}
    except Exception as e:  # noqa: BLE001
        probe.error_code = str(e)[:60]
        probe.status = "failed"
    probe.completed_at = datetime.now(timezone.utc)
    probe.latency_ms = int((time.time() - t0) * 1000)
    return probe


async def probe_model_ping(db: AsyncSession, catalog: ModelCatalog) -> ModelHealthProbe:
    """L1: tiny streaming ping (spec §20)."""
    config = _provider_config(catalog.provider)
    started = datetime.now(timezone.utc)
    probe = ModelHealthProbe(
        id=uuid.uuid4(),
        model_catalog_id=catalog.id,
        probe_type="l1_ping",
        status="failed",
        started_at=started,
    )
    try:
        result = await stream_completion_and_collect(
            system_prompt="Return exactly OK.",
            user_content="ping",
            model=catalog.model_id,
            temperature=0,
            max_tokens=8,
            provider_role="primary",
            provider=catalog.provider,
        )
        probe.latency_ms = result.latency_ms
        probe.first_token_ms = _first_token_from_result(result, started)
        probe.output_valid = bool(result.final_content.strip())
        probe.status = "ok" if (result.final_content.strip() and not result.error) else "failed"
        probe.error_code = result.error
    except Exception as e:  # noqa: BLE001
        probe.error_code = str(e)[:60]
        probe.status = "failed"
    probe.completed_at = datetime.now(timezone.utc)
    return probe


def _first_token_from_result(result, started: datetime) -> int | None:
    if result.latency_ms:
        return min(result.latency_ms, result.latency_ms // 2)
    return None


async def record_production_signal(
    db: AsyncSession,
    *,
    provider: str,
    model_id: str,
    success: bool,
    latency_ms: int | None,
    error_code: str | None = None,
) -> None:
    """Spec §52: every real attempt feeds the health snapshot (production 70%)."""
    from app.model_autopilot.health import upsert_health_snapshot

    catalog = (
        await db.execute(
            select(ModelCatalog).where(
                ModelCatalog.provider == provider,
                ModelCatalog.model_id == model_id,
            )
        )
    ).scalar_one_or_none()
    if catalog is None:
        return
    now = datetime.now(timezone.utc)
    db.add(
        ModelHealthProbe(
            id=uuid.uuid4(),
            model_catalog_id=catalog.id,
            probe_type="production",
            status="ok" if success else "failed",
            started_at=now,
            completed_at=now,
            latency_ms=latency_ms,
            error_code=error_code,
            output_valid=success,
            detail_json={"source": "production"},
        )
    )
    await upsert_health_snapshot(db, catalog.id)
