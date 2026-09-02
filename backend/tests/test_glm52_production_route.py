from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.gateway.model_gateway import _runtime_reasoning_mode
from app.model_autopilot.retired_models import (
    PRODUCTION_MODEL_ID,
    PRODUCTION_MODEL_PROVIDER,
    is_retired_production_model,
    normalize_production_model,
)


def test_only_exact_failed_routes_are_normalized():
    assert normalize_production_model("deepseek-v4-flash") == PRODUCTION_MODEL_ID
    assert normalize_production_model("stepfun-ai/step-3.7-flash") == PRODUCTION_MODEL_ID
    assert normalize_production_model("deepseek-v4-flash-free") == PRODUCTION_MODEL_ID
    assert normalize_production_model("glm-5.3-flash") == "glm-5.3-flash"
    assert is_retired_production_model("deepseek-v4-flash") is True
    assert is_retired_production_model(PRODUCTION_MODEL_ID) is False


def test_glm52_runtime_uses_same_thinking_shape_as_release_gate():
    assert _runtime_reasoning_mode("glm-5.2") == "enabled"
    assert _runtime_reasoning_mode("z-ai/glm-5.2") == "enabled"
    assert _runtime_reasoning_mode("glm-5.3-flash") is None


@pytest.mark.asyncio
async def test_retired_frozen_route_falls_through_to_audited_binding():
    from app.model_autopilot.resolver import resolve_route

    run = SimpleNamespace(
        model_binding_snapshot={
            "roles": {
                "draft_writer": {
                    "primary": {
                        "provider": "new-api",
                        "model": "stepfun-ai/step-3.7-flash",
                    },
                    "fallbacks": [],
                }
            }
        }
    )

    class Rows:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class Session:
        def __init__(self):
            self.calls = 0

        async def execute(self, _statement):
            self.calls += 1
            return Rows(run if self.calls == 1 else None)

    binding = SimpleNamespace(
        provider=PRODUCTION_MODEL_PROVIDER,
        primary_model=PRODUCTION_MODEL_ID,
        fallback_model=None,
        routing_mode="hybrid",
        routing_policy_id=None,
    )
    service = SimpleNamespace(get_binding=AsyncMock(return_value=binding))

    with patch(
        "app.model_autopilot.resolver.ModelBindingService",
        return_value=service,
    ):
        result = await resolve_route(
            Session(),
            agent_role="draft_writer",
            book_id=uuid.uuid4(),
            chapter_run_id=uuid.uuid4(),
        )

    assert result.provider == PRODUCTION_MODEL_PROVIDER
    assert result.model == PRODUCTION_MODEL_ID
    assert result.frozen_snapshot is False


@pytest.mark.asyncio
async def test_legacy_binding_is_fail_safe_before_migration_reaches_it():
    from app.model_autopilot.resolver import resolve_route

    class Rows:
        def scalar_one_or_none(self):
            return None

    class Session:
        async def execute(self, _statement):
            return Rows()

    binding = SimpleNamespace(
        provider="new-api",
        primary_model="deepseek-v4-flash",
        fallback_model="stepfun-ai/step-3.7-flash",
        routing_mode="hybrid",
        routing_policy_id=None,
    )
    service = SimpleNamespace(get_binding=AsyncMock(return_value=binding))

    with patch(
        "app.model_autopilot.resolver.ModelBindingService",
        return_value=service,
    ):
        result = await resolve_route(
            Session(),
            agent_role="review_agent",
            book_id=uuid.uuid4(),
        )

    assert result.provider == PRODUCTION_MODEL_PROVIDER
    assert result.model == PRODUCTION_MODEL_ID
    assert result.fallbacks == []
