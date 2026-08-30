"""v9.6 P0 tests: route weights, config-run constants, manual-stop semantics.

Pure-logic cases run anywhere; DB-backed cases auto-skip without PostgreSQL.
"""
from __future__ import annotations

import pytest

from app.agents.registry import ROLE_REGISTRY, required_roles
from app.model_autopilot.autoconfig_job import REQUIRED_ROLES, ROLE_DISPLAY, VALID_MINUTES
from app.model_autopilot.capability import CONTEXT_REQUIRED_ROLES
from app.model_autopilot.preflight import PREFLIGHT_ROLES
from app.model_autopilot.router import DEFAULT_WEIGHTS


def test_autoconfig_required_roles_complete():
    # v9.7 §11: REQUIRED_ROLES comes from RoleRegistry (production + model_required)
    assert "chapter_planner" in REQUIRED_ROLES
    assert "draft_writer" in REQUIRED_ROLES
    assert "review_agent" in REQUIRED_ROLES
    assert "state_extractor" in REQUIRED_ROLES
    assert "memory_compiler" in REQUIRED_ROLES
    assert "local_rewrite_editor" in REQUIRED_ROLES
    assert "blank_planner" in REQUIRED_ROLES
    assert "evidence_ranker" in REQUIRED_ROLES
    assert REQUIRED_ROLES == required_roles()
    assert PREFLIGHT_ROLES == REQUIRED_ROLES
    assert "memory_compiler" in CONTEXT_REQUIRED_ROLES
    assert "drift_audit" in CONTEXT_REQUIRED_ROLES
    assert all(
        ROLE_REGISTRY[role].expected_context_tokens < 128_000 * 0.95
        for role in REQUIRED_ROLES
    )
    assert ROLE_DISPLAY["draft_writer"] == "DraftWriter"
    assert ROLE_DISPLAY["memory_compiler"] == "MemoryCompiler"
    assert VALID_MINUTES == 30


def test_v96_route_weights():
    assert DEFAULT_WEIGHTS["quality"] == 0.45
    assert DEFAULT_WEIGHTS["reliability"] == 0.20
    assert DEFAULT_WEIGHTS["context"] == 0.15
    assert DEFAULT_WEIGHTS["health"] == 0.10
    assert DEFAULT_WEIGHTS["performance"] == 0.10
    assert round(sum(DEFAULT_WEIGHTS.values()), 2) == 1.00


@pytest.mark.asyncio
async def test_resume_after_failed_preflight_schedules_a_fresh_gate(monkeypatch):
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    import app.services.writing_session_service as service

    old_plan_id = uuid.uuid4()
    session = SimpleNamespace(
        id=uuid.uuid4(),
        status="blocked",
        control_requested="none",
        stop_reason="model_preflight_failed",
        stop_detail={"blockers": [{"code": "NO_ELIGIBLE_MODEL"}]},
        model_preflight_status="blocked",
        model_preflight_detail={"blockers": [{"code": "NO_ELIGIBLE_MODEL"}]},
        model_route_plan_id=old_plan_id,
        resumed_at=None,
    )

    class Result:
        def scalar_one_or_none(self):
            return session

    class Db:
        async def execute(self, _statement):
            return Result()

    record_event = AsyncMock()
    touch_advance = Mock()
    monkeypatch.setattr(service, "_record_event", record_event)
    monkeypatch.setattr(service, "_touch_advance", touch_advance)

    resumed = await service.control_writing_session(
        Db(),
        session_id=session.id,
        action="resume",
    )

    assert resumed.status == "created"
    assert resumed.model_preflight_status == "running"
    assert resumed.model_preflight_detail is None
    assert resumed.model_route_plan_id is None
    assert resumed.stop_reason is None
    assert resumed.stop_detail is None
    assert record_event.await_args.args[3] == {"model_preflight_retry": True}
    dedupe_hint = touch_advance.call_args.kwargs["dedupe_hint"]
    assert dedupe_hint.startswith(f"session-resume:{session.id}:")


@pytest.mark.asyncio
async def test_running_session_cannot_bypass_blocked_preflight(monkeypatch):
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.services.writing_session_controller as controller

    session = SimpleNamespace(
        id=uuid.uuid4(),
        status="running",
        control_requested="none",
        policy_snapshot={},
        model_preflight_status="blocked",
        model_preflight_detail={"blockers": [{"code": "NO_ELIGIBLE_MODEL"}]},
        stop_reason=None,
        stop_detail=None,
    )
    record_event = AsyncMock()
    monkeypatch.setattr(controller, "_record_event", record_event)

    decision = await controller.evaluate_session(object(), session)

    assert decision.action == "block"
    assert session.status == "blocked"
    assert session.stop_reason == "model_preflight_failed"
    assert session.stop_detail == {
        "blockers": [{"code": "NO_ELIGIBLE_MODEL"}]
    }
    record_event.assert_awaited_once()


def _db_available() -> bool:
    try:
        import asyncio

        import asyncpg

        from app.config import settings

        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

        async def _probe() -> bool:
            # Connect and close on ONE loop: closing on a second asyncio.run
            # uses a transport bound to a dead loop and silently skips tests.
            conn = await asyncpg.connect(dsn=dsn, timeout=3)
            await conn.close()
            return True

        return bool(asyncio.run(_probe()))
    except Exception:  # noqa: BLE001 - no PG here means skip
        return False


DB = _db_available()
requires_db = pytest.mark.skipif(not DB, reason="PostgreSQL not reachable")


@requires_db
@pytest.mark.asyncio
async def test_v96_manual_stop_immediate_without_run():
    """Spec §105: created + no run → cancel lands immediately, no chapter."""
    import uuid

    from app.database import async_session_factory
    from app.models import Book
    from app.schemas.writing_session import WritingSessionCreateRequest
    from app.services.writing_session_service import (
        control_writing_session,
        create_writing_session,
    )

    async with async_session_factory() as db:
        book_id = uuid.uuid4()
        db.add(Book(id=book_id, title="v96"))
        await db.flush()
        created = await create_writing_session(
            db,
            book_id=book_id,
            req=WritingSessionCreateRequest(mode="manual"),
            idempotency_key="v96-stop",
        )
        sid = created.id
        await db.commit()

    async with async_session_factory() as db:
        stopped = await control_writing_session(
            db, session_id=uuid.UUID(str(sid)), action="cancel"
        )
        await db.commit()
        assert stopped.status == "cancelled"
        assert stopped.stop_reason == "manual_stop"
        assert stopped.completed_at is not None


@requires_db
@pytest.mark.asyncio
async def test_v96_health_zero_rate_retained():
    """Spec §107: production 0% must not fall back to probe rate."""
    import uuid
    from datetime import datetime, timezone

    from sqlalchemy import delete, select

    from app.database import async_session_factory
    from app.model_autopilot.health import upsert_health_snapshot
    from app.models import ModelCatalog, ModelHealthProbe, ModelHealthSnapshot

    async with async_session_factory() as db:
        # Shared test DB: drop this test's fixed-identity leftovers first so
        # re-running the suite never trips the catalog unique constraint.
        stale = (await db.execute(
            select(ModelCatalog).where(
                ModelCatalog.provider == "p", ModelCatalog.model_id == "m-0pct"
            )
        )).scalars().all()
        for c in stale:
            await db.execute(delete(ModelHealthProbe).where(ModelHealthProbe.model_catalog_id == c.id))
            await db.execute(delete(ModelHealthSnapshot).where(ModelHealthSnapshot.model_catalog_id == c.id))
            await db.execute(delete(ModelCatalog).where(ModelCatalog.id == c.id))
        await db.commit()

        catalog = ModelCatalog(
            id=uuid.uuid4(),
            provider="p",
            model_id="m-0pct",
            availability_status="available",
            discovery_source="manual",
        )
        db.add(catalog)
        await db.flush()
        now = datetime.now(timezone.utc)
        db.add(
            ModelHealthProbe(
                id=uuid.uuid4(),
                model_catalog_id=catalog.id,
                probe_type="production",
                status="failed",
                started_at=now,
                completed_at=now,
                error_code="HTTP_503",
            )
        )
        db.add(
            ModelHealthProbe(
                id=uuid.uuid4(),
                model_catalog_id=catalog.id,
                probe_type="l1_ping",
                status="ok",
                started_at=now,
                completed_at=now,
                latency_ms=100,
            )
        )
        await db.commit()
        await upsert_health_snapshot(db, catalog.id)
        await db.commit()
        snap = (
            await db.execute(
                ModelHealthSnapshot.__table__.select().where(
                    ModelHealthSnapshot.model_catalog_id == catalog.id
                )
            )
        ).first()
        # production 0% retained for 15m (never replaced by the 100% probe)
        assert snap.success_rate_15m == 0.0
