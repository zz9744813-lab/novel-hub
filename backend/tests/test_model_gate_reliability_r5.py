"""Regression tests for release model-route evidence and health hard errors."""
from __future__ import annotations

import uuid
import asyncio
from types import SimpleNamespace

from app.agents.registry import required_roles
from app.model_autopilot.health import classify_health
from app.production_pack import model_evidence


def test_effective_targets_includes_primary_and_fallback_routes(monkeypatch):
    primary = "primary-model"
    fallback = "fallback-model"

    async def get_binding(_self, role, _book_id):
        return SimpleNamespace(
            provider="new-api",
            primary_model=primary,
            fallback_model=fallback,
        )

    monkeypatch.setattr(model_evidence.ModelBindingService, "get_binding", get_binding)
    targets, missing = asyncio.run(
        model_evidence._effective_targets(SimpleNamespace(), uuid.uuid4())
    )
    assert not missing
    assert targets.route_targets[("new-api", primary, "primary")] == set(
        required_roles()
    )
    assert targets.route_targets[("new-api", fallback, "fallback")] == set(
        required_roles()
    )


def test_latest_production_auth_error_overrides_successful_l1_probe():
    assert (
        classify_health(
            probe_ok_recent=1.0,
            prod_15m=1.0,
            consecutive_failures=0,
            last_probe_status="ok",
            last_error=None,
            has_valid_probe=True,
            last_production_error="HTTP_401",
        )
        == "unavailable"
    )
    assert (
        classify_health(
            probe_ok_recent=1.0,
            prod_15m=1.0,
            consecutive_failures=0,
            last_probe_status="ok",
            last_error=None,
            has_valid_probe=True,
            last_production_error=None,
        )
        == "healthy"
    )


def test_configured_alias_is_promoted_only_after_valid_text_handshake():
    from app.model_autopilot.classification import promote_configured_text_model

    catalog = SimpleNamespace(
        text_generation_eligible=False,
        auto_route_enabled=False,
        model_kind="unknown",
        discovery_source="configured_binding",
        input_modalities=[],
        output_modalities=[],
    )
    promote_configured_text_model(catalog)
    assert catalog.text_generation_eligible is True
    assert catalog.auto_route_enabled is True
