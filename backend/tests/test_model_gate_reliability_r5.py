"""Regression tests for release model-route evidence and health hard errors."""
from __future__ import annotations

import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

from app.agents.registry import required_roles
from app.model_autopilot.health import classify_health, upsert_health_snapshot
from app.models import AgentModelBinding, ModelCatalog, ModelHealthProbe
from app.production_pack import model_evidence
from tests.test_v98_model_evidence import FakeAsyncSession


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


def test_health_rows_keep_auth_failure_until_later_production_success():
    async def scenario():
        db = FakeAsyncSession()
        catalog = ModelCatalog(
            id=uuid.uuid4(), provider="new-api", model_id="alias",
            enabled=True, auto_route_enabled=True,
            availability_status="available", model_kind="text_generation",
            text_generation_eligible=True, metadata_json={},
        )
        now = datetime.now(timezone.utc)
        # Insert newest first because FakeAsyncSession preserves query order.
        db.add(ModelHealthProbe(
            id=uuid.uuid4(), model_catalog_id=catalog.id, probe_type="l1_ping",
            status="ok", output_valid=True, started_at=now,
        ))
        db.add(ModelHealthProbe(
            id=uuid.uuid4(), model_catalog_id=catalog.id, probe_type="production",
            status="failed", error_code="HTTP_401", started_at=now - timedelta(seconds=1),
        ))
        await db.flush()
        first = await upsert_health_snapshot(db, catalog.id)
        assert first.health_status == "unavailable"

        later_success = ModelHealthProbe(
            id=uuid.uuid4(), model_catalog_id=catalog.id, probe_type="production",
            status="ok", output_valid=True, started_at=now + timedelta(seconds=1),
        )
        db.add(later_success)
        await db.flush()
        db._store[ModelHealthProbe.__tablename__].remove(later_success)
        db._store[ModelHealthProbe.__tablename__].insert(0, later_success)
        second = await upsert_health_snapshot(db, catalog.id)
        assert second.health_status != "unavailable"

    asyncio.run(scenario())


def test_configured_model_absent_from_models_gets_exact_handshake(monkeypatch):
    """Exercise bootstrap's configured alias path, including failed persistence."""
    import app.model_autopilot.preflight as preflight

    async def run(status: str):
        db = FakeAsyncSession()
        binding = AgentModelBinding(
            id=uuid.uuid4(), scope_type="global", agent_role="draft_writer",
            provider="new-api", primary_model="private-alias", fallback_model=None,
            updated_by="test", updated_at=datetime.now(timezone.utc),
        )
        db.add(binding)
        await db.flush()

        class Context:
            async def __aenter__(self): return db
            async def __aexit__(self, *_): return False

        async def fake_probe(_db, catalog, **_kwargs):
            return ModelHealthProbe(
                id=uuid.uuid4(), model_catalog_id=catalog.id, probe_type="l1_ping",
                status=status, output_valid=status == "ok",
                started_at=datetime.now(timezone.utc),
                error_code=None if status == "ok" else "HTTP_401",
            )

        monkeypatch.setattr(preflight, "_provider_sync_list", lambda _db: asyncio.sleep(0, result=[("new-api", "http://unused", "key")]))
        monkeypatch.setattr(preflight, "sync_catalog_from_provider", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))
        monkeypatch.setattr(preflight, "probe_model_ping", fake_probe)
        monkeypatch.setattr(preflight, "ensure_capability_for_catalog", lambda *_args, **_kwargs: asyncio.sleep(0))
        import app.database as database
        monkeypatch.setattr(database, "async_session_factory", lambda: Context())
        monkeypatch.setenv("NEW_API_BASE_URL", "http://unused")
        monkeypatch.setenv("NEW_API_API_KEY", "key")
        report = await preflight.bootstrap_catalog_and_probes()
        catalogs = db._store[ModelCatalog.__tablename__]
        assert len(catalogs) == 1
        assert catalogs[0].discovery_source == "configured_binding"
        assert catalogs[0].availability_status == ("available" if status == "ok" else "missing")
        assert catalogs[0].auto_route_enabled is (status == "ok")
        return report

    asyncio.run(run("ok"))
    asyncio.run(run("failed"))


def test_release_gate_blocks_unproven_fallback_route(monkeypatch):
    from unittest.mock import AsyncMock, patch

    primary = ModelCatalog(
        id=uuid.uuid4(), provider="new-api", model_id="primary",
        enabled=True, auto_route_enabled=True, availability_status="available",
        model_kind="text_generation", text_generation_eligible=True, metadata_json={},
    )
    fallback = ModelCatalog(
        id=uuid.uuid4(), provider="new-api", model_id="fallback",
        enabled=True, auto_route_enabled=True, availability_status="available",
        model_kind="text_generation", text_generation_eligible=True, metadata_json={},
    )
    now = datetime.now(timezone.utc)
    primary_snapshot = SimpleNamespace(health_status="healthy", last_probe_at=now)
    fallback_snapshot = SimpleNamespace(health_status="healthy", last_probe_at=now)
    targets = model_evidence.EffectiveTargets(
        {("new-api", "primary"): {"draft_writer"}},
        {
            ("new-api", "primary", "primary"): {"draft_writer"},
            ("new-api", "fallback", "fallback"): {"draft_writer"},
        },
    )

    class Rows:
        def __init__(self, value): self.value = value
        def scalar_one_or_none(self): return self.value

    class Session:
        def __init__(self):
            self.calls = 0
            self.added = []
        async def execute(self, _statement):
            self.calls += 1
            if self.calls % 2:
                return Rows(fallback if self.calls == 1 else primary)
            return Rows(fallback_snapshot if self.calls == 2 else primary_snapshot)
        def add(self, value): self.added.append(value)
        async def commit(self): return None
        async def refresh(self, _value): return None

    session = Session()
    class Context:
        async def __aenter__(self): return session
        async def __aexit__(self, *_): return False

    passing = {"status": "succeeded", "execution_complete": True, "gateway_calls": 0, "reused": True}
    primary_state = {"ability": {"state": "valid"}, "context": {"state": "valid"}, "role_evidence": {"draft_writer": {"state": "valid", "passed": True}}, "context_profile": {"effective": 1}}
    fallback_state = {"ability": {"state": "invalid"}, "context": {"state": "invalid"}, "role_evidence": {"draft_writer": {"state": "invalid", "passed": False}}, "context_profile": {"effective": None}}

    async def state(_db, catalog):
        return primary_state if catalog.model_id == "primary" else fallback_state

    async def qualify(_db, _run, force=False): return passing

    with patch("app.database.async_session_factory", return_value=Context()), \
        patch("app.main.ensure_required_bindings", AsyncMock()), \
        patch.object(model_evidence, "bootstrap_catalog_and_probes", AsyncMock(return_value={})), \
        patch.object(model_evidence, "_reconcile_known_model_aliases", AsyncMock(return_value={"changed": [], "unresolved": []})), \
        patch.object(model_evidence, "_effective_targets", AsyncMock(return_value=(targets, []))), \
        patch.object(model_evidence, "_apply_role_assignments", AsyncMock(return_value=[])), \
        patch("app.model_eval.engine.run_qualification", qualify), \
        patch("app.model_eval.engine.run_context_ladder", qualify), \
        patch("app.model_eval.engine.get_catalog_evidence_state", state), \
        patch.object(model_evidence, "_required_context", return_value=0):
        report = asyncio.run(model_evidence.ensure_configured_model_evidence(SimpleNamespace(pack_id="p", revision=1)))

    assert report["passed"] is False
    assert any(item.get("route_kind") == "fallback" for item in report["blockers"]), report


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
