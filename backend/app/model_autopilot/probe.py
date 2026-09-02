"""Model autopilot: health probes (spec §20–§21, §24, §26–§28).

L0 provider probe, L1 model ping (tiny stream), L2 capability probe (low-freq).
Probe concurrency is capped; never competes with an active chapter pipeline.
"""
from __future__ import annotations

import asyncio
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


def _health_probe_max_tokens() -> int:
    try:
        configured = int(os.environ.get("MODEL_HEALTH_MAX_TOKENS", "128"))
    except ValueError:
        configured = 128
    return max(8, min(512, configured))


def _configured_handshake_max_tokens() -> int:
    try:
        configured = int(os.environ.get("MODEL_HANDSHAKE_MAX_TOKENS", "2048"))
    except ValueError:
        configured = 2048
    return max(512, min(4096, configured))


def _health_probe_read_timeout() -> int:
    """Bound recurring connectivity checks independently from long model work."""

    try:
        configured = int(os.environ.get("MODEL_HEALTH_READ_TIMEOUT_SECONDS", "120"))
    except ValueError:
        configured = 120
    return max(15, min(300, configured))


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


async def probe_model_ping(
    db: AsyncSession,
    catalog: ModelCatalog,
    *,
    allow_reasoning_retry: bool = False,
) -> ModelHealthProbe:
    """L1: tiny streaming ping (spec §20)."""
    started = datetime.now(timezone.utc)
    probe = ModelHealthProbe(
        id=uuid.uuid4(),
        model_catalog_id=catalog.id,
        probe_type="l1_ping",
        status="failed",
        started_at=started,
    )
    try:
        transient_errors = {
            "CONNECT_TIMEOUT",
            "HTTP_429",
            "HTTP_500",
            "HTTP_502",
            "HTTP_503",
            "HTTP_504",
        }
        max_attempts = 4 if allow_reasoning_retry else 1
        use_handshake_budget = False
        adaptive_retry = False
        error_history: list[str] = []
        first_error = None
        first_finish_reason = None
        result = None
        for attempt in range(1, max_attempts + 1):
            result = await stream_completion_and_collect(
                system_prompt="Return exactly OK.",
                user_content="ping",
                model=catalog.model_id,
                temperature=0,
                # Reasoning-capable OpenAI-compatible models may spend a tiny
                # output budget before emitting final text. The configured
                # handshake can retry with a larger cap, while recurring L1
                # probes remain single-attempt and cheap.
                max_tokens=(
                    _configured_handshake_max_tokens()
                    if use_handshake_budget
                    else _health_probe_max_tokens()
                ),
                provider_role="primary",
                provider=catalog.provider,
                reasoning_mode="disabled",
                read_timeout_seconds=_health_probe_read_timeout(),
            )
            if attempt == 1:
                first_error = result.error
                first_finish_reason = result.finish_reason
            if result.error:
                error_history.append(result.error)
            if not allow_reasoning_retry or not result.error:
                break
            if result.error == "final_content_empty":
                adaptive_retry = True
                use_handshake_budget = True
                continue
            if result.error not in transient_errors or attempt >= max_attempts:
                break
            await asyncio.sleep(float(4 * (2 ** (attempt - 1))))
        assert result is not None
        probe.latency_ms = result.latency_ms
        probe.first_token_ms = result.first_token_ms  # measured TTFT (v9.6 §44)
        probe.output_valid = bool(result.final_content.strip())
        probe.status = "ok" if (result.final_content.strip() and not result.error) else "failed"
        probe.error_code = result.error
        probe.detail_json = {
            "finish_reason": result.finish_reason,
            "reasoning_chars": len(result.reasoning_text),
            "adaptive_retry": adaptive_retry,
            "attempt_count": attempt,
            "error_history": error_history,
            "first_error": first_error if attempt > 1 else None,
            "first_finish_reason": first_finish_reason if attempt > 1 else None,
        }
    except Exception as e:  # noqa: BLE001
        probe.error_code = str(e)[:60]
        probe.status = "failed"
    probe.completed_at = datetime.now(timezone.utc)
    return probe


# v9.6 §43: performance probe — ~128 token generation, stream, temperature 0.
PERFORMANCE_PROMPT = (
    "请写一段约128字的短文：描述一座晚霞中的湖，语言平实、带一点细节。只输出正文。"
)


async def probe_model_performance(db: AsyncSession, catalog: ModelCatalog) -> ModelHealthProbe:
    """Real throughput probe: TTFT, latency, tokens/sec (spec §43, §47)."""
    started = datetime.now(timezone.utc)
    probe = ModelHealthProbe(
        id=uuid.uuid4(),
        model_catalog_id=catalog.id,
        probe_type="performance",
        status="failed",
        started_at=started,
    )
    try:
        result = await stream_completion_and_collect(
            system_prompt=PERFORMANCE_PROMPT,
            user_content="写吧。",
            model=catalog.model_id,
            temperature=0,
            max_tokens=180,
            provider_role="primary",
            provider=catalog.provider,
        )
        probe.latency_ms = result.latency_ms
        probe.first_token_ms = result.first_token_ms
        probe.prompt_tokens = result.prompt_tokens or 0
        probe.completion_tokens = result.completion_tokens or 0
        generation_ms = max(
            0,
            (result.latency_ms or 0) - (result.first_token_ms or 0),
        )
        if result.completion_tokens and generation_ms > 0:
            probe.tokens_per_second = round(
                result.completion_tokens / (generation_ms / 1000.0), 2
            )
        probe.output_valid = len(result.final_content.strip()) >= 20
        probe.status = "ok" if probe.output_valid and not result.error else "failed"
        probe.error_code = result.error
    except Exception as e:  # noqa: BLE001
        probe.error_code = str(e)[:60]
        probe.status = "failed"
    probe.completed_at = datetime.now(timezone.utc)
    return probe


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
