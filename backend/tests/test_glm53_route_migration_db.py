"""Real PostgreSQL migration: exact targets, audit, fallback and idempotence."""
import importlib.util
from pathlib import Path
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import select

from app.database import async_session_factory
from app.models import AgentModelBinding, ModelChangeLog


@pytest.mark.asyncio
async def test_glm53_migration_is_audited_and_does_not_rewrite_healthy_routes():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0026_glm53_flash_route.py"
    spec = importlib.util.spec_from_file_location("glm53_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with async_session_factory() as db:
        rows = [
            AgentModelBinding(
                id=uuid.uuid4(), scope_type="book", scope_id=uuid.uuid4(),
                agent_role="draft_writer", provider=provider, primary_model=primary,
                fallback_model=fallback, reasoning_mode="disabled", version=7,
                updated_by="migration-test", allowed_model_ids=["stale-id"],
                manual_primary_locked=True, manual_fallback_locked=True,
            )
            for provider, primary, fallback in (
                ("new-api", "glm-5.2", "approved-fallback"),
                ("primary", "approved-primary", "z-ai/glm-5.2"),
                ("new-api", "untouched-primary", "untouched-fallback"),
                ("other-provider", "glm-5.2", "z-ai/glm-5.2"),
                ("openrouter", "z-ai/glm-5.2", "approved-fallback"),
            )
        ]
        db.add_all(rows)
        try:
            await db.flush()
            connection = await db.connection()

            def run_twice(conn):
                with patch.object(migration.op, "get_bind", return_value=conn):
                    migration.upgrade()
                    migration.upgrade()

            await connection.run_sync(run_twice)
            for row in rows:
                await db.refresh(row)
            assert (rows[0].primary_model, rows[0].fallback_model, rows[0].version) == (
                "glm-5.3-flash", "approved-fallback", 8,
            )
            assert rows[0].reasoning_mode == "auto"
            assert rows[0].allowed_model_ids == []
            assert rows[0].manual_primary_locked is False
            assert rows[0].manual_fallback_locked is True
            assert (rows[1].primary_model, rows[1].fallback_model, rows[1].version) == (
                "approved-primary", None, 8,
            )
            assert rows[1].allowed_model_ids == ["stale-id"]
            assert rows[1].provider == "primary"
            assert rows[1].manual_primary_locked is True
            assert rows[1].manual_fallback_locked is False
            assert (rows[2].primary_model, rows[2].fallback_model, rows[2].version) == (
                "untouched-primary", "untouched-fallback", 7,
            )
            assert (rows[3].provider, rows[3].primary_model, rows[3].version) == (
                "other-provider", "glm-5.2", 7,
            )
            assert (rows[4].provider, rows[4].primary_model, rows[4].version) == (
                "openrouter", "z-ai/glm-5.2", 7,
            )
            audit = (await db.execute(select(ModelChangeLog).where(
                ModelChangeLog.binding_id.in_([row.id for row in rows])
            ))).scalars().all()
            assert len(audit) == 2
            assert {entry.changed_by for entry in audit} == {"release:0026_glm53_flash_route"}
            fallback_audit = next(entry for entry in audit if entry.binding_id == rows[1].id)
            assert "'z-ai/glm-5.2' -> None" in fallback_audit.reason
        finally:
            await db.rollback()
