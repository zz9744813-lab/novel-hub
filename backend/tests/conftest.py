"""Pytest configuration and fixtures."""
import sys
import os
import asyncio

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set test environment variables before importing app modules
os.environ.setdefault("POSTGRES_DB", "novelforge_test")
os.environ.setdefault("POSTGRES_USER", "novelforge")
os.environ.setdefault("POSTGRES_PASSWORD", "NovelForge2026Secure")
os.environ.setdefault("PRIMARY_BASE_URL", "http://127.0.0.1:3000/v1")
os.environ.setdefault("PRIMARY_API_KEY", "sk-test-key")
os.environ.setdefault("LOG_LEVEL", "DEBUG")

# ── Session-scoped event loop (must run before any app module import) ──
# pytest-asyncio 9.x uses per-function loop by default, but asyncpg engine
# is a module-level singleton that creates Future objects on the import-time
# loop.  We create a session-scoped loop here and patch the engine before
# any test runs, so every test shares the same loop.
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from app.config import settings
import app.database as db_mod

_engine = create_async_engine(
    settings.database_url,
    poolclass=NullPool,
    echo=False,
)
db_mod.engine = _engine
db_mod.async_session_factory = async_sessionmaker(
    _engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

import pytest


@pytest.fixture(scope="session")
def event_loop():
    yield _loop


@pytest.fixture(scope="session", autouse=True)
def _close_engine():
    """Dispose engine at end of session."""
    yield
    _loop.run_until_complete(_engine.dispose())
    _loop.close()


@pytest.fixture
def sample_story_event():
    """Sample story event for retrieval tests."""
    return {
        "event_id": "evt-001",
        "event_type": "combat",
        "chapter_no": 5,
        "certainty": "explicit",
        "evidence_excerpt": "The knight drew his sword...",
        "subject_entity_ids": ["char-001"],
        "object_entity_ids": ["char-002"],
    }


@pytest.fixture
def sample_ft_candidate():
    """Sample full-text search candidate."""
    return {
        "id": "ft-001",
        "chapter_no": 3,
        "scene_no": 2,
        "scene_summary": "A fierce battle erupted in the northern pass.",
        "rank": 0.85,
    }


@pytest.fixture
def sample_query_plan():
    """Sample query plan for scoring tests."""
    return {
        "character_ids": ["char-001", "char-003"],
        "event_types": ["combat", "betrayal"],
        "chapter_range": {"from": 1, "to": 10},
        "exact_terms": ["sword", "battle"],
        "semantic_questions": ["What caused the battle?"],
    }
