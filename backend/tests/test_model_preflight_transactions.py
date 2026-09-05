"""Real async ORM regressions for rollback and network transaction boundaries.

SQLite supplies actual SQLAlchemy expiration/rollback semantics here; only the
provider HTTP request is replaced. These are not FakeAsyncSession tests.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles, deregister
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    AgentModelBinding,
    ModelCapabilityProfile,
    ModelCatalog,
    ModelHealthProbe,
    ModelHealthSnapshot,
)
from app.model_autopilot import preflight


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_phase", ["probe", "persist", "none"])
async def test_probe_failure_does_not_expire_the_next_model(
    tmp_path, monkeypatch, failure_phase
):
    import app.database as database

    # Use the production ORM mappings. Only the PostgreSQL JSONB DDL spelling
    # needs a SQLite equivalent; JSON binding and ORM sessions are real.
    compiles(JSONB, "sqlite")(lambda *_args, **_kwargs: "JSON")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'probes.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tables = [
        model.__table__ for model in (
            AgentModelBinding, ModelCatalog, ModelHealthProbe,
            ModelHealthSnapshot, ModelCapabilityProfile,
        )
    ]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda conn: ModelCatalog.metadata.create_all(conn, tables=tables)
            )
        async with factory() as db:
            for index, name in enumerate(("a-fails", "b-works")):
                db.add(AgentModelBinding(
                    id=uuid.uuid4(), scope_type="global",
                    agent_role=("draft_writer", "review_agent")[index],
                    provider="new-api", primary_model=name, updated_by="test",
                ))
                db.add(ModelCatalog(
                    id=uuid.uuid4(), provider="new-api", model_id=name,
                    enabled=True, availability_status="available",
                    text_generation_eligible=True, auto_route_enabled=True,
                ))
            await db.commit()

        seen = []
        held_transactions = []

        async def probe(db, catalog, **_kwargs):
            name = catalog.model_id
            seen.append(name)
            held_transactions.append(bool(db.in_transaction()))
            if name == "a-fails" and failure_phase == "probe":
                raise RuntimeError("injected provider failure")
            return ModelHealthProbe(
                id=uuid.uuid4(), model_catalog_id=catalog.id,
                probe_type="l1_ping", started_at=datetime.now(timezone.utc),
                status=None if name == "a-fails" and failure_phase == "persist" else "ok",
                output_valid=True, latency_ms=1,
            )

        monkeypatch.setattr(database, "async_session_factory", factory)
        monkeypatch.setattr(preflight, "_provider_sync_list", AsyncMock(return_value=[]))
        monkeypatch.setattr(preflight, "probe_model_ping", probe)
        report = await preflight.bootstrap_catalog_and_probes()

        assert seen == ["a-fails", "b-works"]
        assert held_transactions == [False, False]
        expected_count = 2 if failure_phase == "none" else 1
        assert report["probed"] == expected_count
        if failure_phase != "none":
            assert report["errors"][0]["model"] == "a-fails"
            assert report["errors"][0]["error"] == (
                "RuntimeError" if failure_phase == "probe" else "IntegrityError"
            )
        else:
            assert report["errors"] == []
        async with factory() as db:
            persisted = (await db.execute(select(ModelHealthProbe))).scalars().all()
            assert len(persisted) == expected_count
            assert all(row.status == "ok" for row in persisted)
    finally:
        await engine.dispose()
        deregister(JSONB)
