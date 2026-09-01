"""Health checks use a short timeout and do not inherit long writing limits."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.gateway.model_gateway import StreamResult
from app.model_autopilot.probe import (
    _health_probe_read_timeout,
    probe_model_ping,
)


def test_health_probe_timeout_default_and_bounds(monkeypatch):
    monkeypatch.delenv("MODEL_HEALTH_READ_TIMEOUT_SECONDS", raising=False)
    assert _health_probe_read_timeout() == 120

    monkeypatch.setenv("MODEL_HEALTH_READ_TIMEOUT_SECONDS", "1")
    assert _health_probe_read_timeout() == 15

    monkeypatch.setenv("MODEL_HEALTH_READ_TIMEOUT_SECONDS", "999")
    assert _health_probe_read_timeout() == 300


@pytest.mark.asyncio
async def test_ping_passes_short_timeout_to_every_adaptive_attempt(monkeypatch):
    monkeypatch.delenv("MODEL_HEALTH_READ_TIMEOUT_SECONDS", raising=False)
    catalog = SimpleNamespace(
        id=uuid.uuid4(),
        model_id="deepseek-v4-flash",
        provider="new-api",
    )
    gateway = AsyncMock(
        side_effect=[
            StreamResult(error="final_content_empty", reasoning_text="thinking"),
            StreamResult(final_content="OK"),
        ]
    )

    with patch(
        "app.model_autopilot.probe.stream_completion_and_collect",
        gateway,
    ):
        result = await probe_model_ping(None, catalog, allow_reasoning_retry=True)

    assert result.status == "ok"
    assert gateway.await_count == 2
    assert {
        call.kwargs["read_timeout_seconds"] for call in gateway.await_args_list
    } == {120}
