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


@pytest.mark.asyncio
async def test_configured_handshake_retries_transient_relay_errors_four_times():
    catalog = SimpleNamespace(
        id=uuid.uuid4(),
        model_id="glm-5.2",
        provider="new-api",
    )
    gateway = AsyncMock(
        side_effect=[
            StreamResult(error="HTTP_500"),
            StreamResult(error="HTTP_503"),
            StreamResult(error="HTTP_500"),
            StreamResult(final_content="OK"),
        ]
    )

    with (
        patch(
            "app.model_autopilot.probe.stream_completion_and_collect",
            gateway,
        ),
        patch("app.model_autopilot.probe.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await probe_model_ping(None, catalog, allow_reasoning_retry=True)

    assert result.status == "ok"
    assert gateway.await_count == 4
    assert result.detail_json["attempt_count"] == 4
    assert result.detail_json["error_history"] == [
        "HTTP_500",
        "HTTP_503",
        "HTTP_500",
    ]
    assert {
        call.kwargs["max_tokens"] for call in gateway.await_args_list
    } == {2048}
    assert {
        call.kwargs["reasoning_mode"] for call in gateway.await_args_list
    } == {"enabled"}


@pytest.mark.asyncio
async def test_configured_handshake_retries_http_200_empty_text_output():
    catalog = SimpleNamespace(
        id=uuid.uuid4(),
        model_id="glm-5.2",
        provider="new-api",
    )
    gateway = AsyncMock(
        side_effect=[
            StreamResult(),
            StreamResult(final_content="OK"),
        ]
    )

    with (
        patch(
            "app.model_autopilot.probe.stream_completion_and_collect",
            gateway,
        ),
        patch("app.model_autopilot.probe.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await probe_model_ping(None, catalog, allow_reasoning_retry=True)

    assert result.status == "ok"
    assert gateway.await_count == 2
    assert result.detail_json["attempt_count"] == 2
    assert result.detail_json["first_error"] == "empty_text_output"
    assert result.detail_json["error_history"] == ["empty_text_output"]


@pytest.mark.asyncio
async def test_configured_handshake_does_not_retry_authentication_failure():
    catalog = SimpleNamespace(
        id=uuid.uuid4(),
        model_id="glm-5.2",
        provider="new-api",
    )
    gateway = AsyncMock(return_value=StreamResult(error="HTTP_401"))

    with (
        patch(
            "app.model_autopilot.probe.stream_completion_and_collect",
            gateway,
        ),
        patch("app.model_autopilot.probe.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        result = await probe_model_ping(None, catalog, allow_reasoning_retry=True)

    assert result.status == "failed"
    assert gateway.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_recurring_health_probe_remains_single_attempt_on_transient_error():
    catalog = SimpleNamespace(
        id=uuid.uuid4(),
        model_id="glm-5.2",
        provider="new-api",
    )
    gateway = AsyncMock(return_value=StreamResult(error="HTTP_500"))

    with (
        patch(
            "app.model_autopilot.probe.stream_completion_and_collect",
            gateway,
        ),
        patch("app.model_autopilot.probe.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        result = await probe_model_ping(None, catalog)

    assert result.status == "failed"
    assert gateway.await_count == 1
    sleep.assert_not_awaited()
