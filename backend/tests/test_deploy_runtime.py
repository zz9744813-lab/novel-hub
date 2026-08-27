from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.startup_checks import check_runtime_ready


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPOSITORY_ROOT / "deploy" / "docker-compose.yml"
POSTGRES_DOCKERFILE = REPOSITORY_ROOT / "deploy" / "postgres" / "Dockerfile"
REDIS_DOCKERFILE = REPOSITORY_ROOT / "deploy" / "redis" / "Dockerfile"
RELEASE_SCRIPT = REPOSITORY_ROOT / "deploy" / "ops" / "novelforge-release"


def test_runtime_configs_are_baked_into_images_not_secret_adjacent_bind_mounts():
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    postgres_image = POSTGRES_DOCKERFILE.read_text(encoding="utf-8")
    redis_image = REDIS_DOCKERFILE.read_text(encoding="utf-8")
    release = RELEASE_SCRIPT.read_text(encoding="utf-8")

    assert "image: novelforge-postgres:16" in compose
    assert "image: novelforge-redis:7" in compose
    assert "./postgres/postgresql.conf:/etc/postgresql" not in compose
    assert "./postgres/pg_hba.conf:/etc/postgresql" not in compose
    assert "./redis/redis.conf:/usr/local/etc/redis" not in compose
    assert "./.env:/app/.env" not in compose
    assert "chown postgres:postgres /etc/postgresql" in postgres_image
    assert "chmod 0755 /etc/postgresql" in postgres_image
    assert "--chown=postgres:postgres --chmod=0644 postgresql.conf" in postgres_image
    assert "--chown=postgres:postgres --chmod=0644 pg_hba.conf" in postgres_image
    assert "chown redis:redis /usr/local/etc/redis" in redis_image
    assert "chmod 0755 /usr/local/etc/redis" in redis_image
    assert "--chown=redis:redis --chmod=0644 redis.conf" in redis_image
    assert 'build postgres redis api worker web' in release


@pytest.mark.asyncio
async def test_runtime_readiness_refreshes_database_without_reprobing_provider():
    cached = {"db": "old result", "provider": "ok", "bindings": "ok"}

    with patch(
        "app.startup_checks.check_db_ready",
        AsyncMock(return_value=(False, "database unavailable")),
    ):
        ready, detail = await check_runtime_ready(cached)

    assert ready is False
    assert detail == {
        "db": "database unavailable",
        "provider": "ok",
        "bindings": "ok",
    }
    assert cached["db"] == "old result"

    with patch(
        "app.startup_checks.check_db_ready",
        AsyncMock(return_value=(True, "ok")),
    ):
        ready, detail = await check_runtime_ready(cached)

    assert ready is True
    assert detail["db"] == "ok"
