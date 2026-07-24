"""Pytest configuration and fixtures."""
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Set test environment variables before importing app modules
os.environ.setdefault("POSTGRES_DB", "novelforge_test")
os.environ.setdefault("POSTGRES_USER", "novelforge")
os.environ.setdefault("POSTGRES_PASSWORD", "NovelForge2026Secure")
os.environ.setdefault("PRIMARY_BASE_URL", "http://127.0.0.1:3000/v1")
os.environ.setdefault("PRIMARY_API_KEY", "sk-test-key")
os.environ.setdefault("LOG_LEVEL", "DEBUG")

import pytest


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
