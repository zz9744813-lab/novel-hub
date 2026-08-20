"""P0-05 contract tests for the global task service."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.services.task_service import (
    build_task_id,
    normalize_task_type,
    parse_task_id,
    paginate_tasks,
    task_actions,
    serialize_chapter_run,
    serialize_import_session,
    serialize_research_session,
)


BOOK_ID = uuid4()
CHAPTER_ID = uuid4()
RUN_ID = uuid4()


def test_task_id_is_type_scoped_and_round_trips_without_uuid_collision():
    assert build_task_id("chapter", RUN_ID) == f"chapter:{RUN_ID}"
    assert build_task_id("import", RUN_ID) == f"import:{RUN_ID}"
    assert normalize_task_type("chapter") == "chapter"
    with pytest.raises(ValueError):
        normalize_task_type("unknown")


def test_task_id_parser_rejects_ambiguous_or_malformed_ids():
    assert parse_task_id(f"chapter:{RUN_ID}") == ("chapter", RUN_ID)
    assert parse_task_id(f"import:{RUN_ID}") == ("import", RUN_ID)
    with pytest.raises(ValueError):
        parse_task_id(str(RUN_ID))
    with pytest.raises(ValueError):
        parse_task_id("chapter:not-a-uuid")


def test_paginate_tasks_returns_stable_page_metadata():
    rows = [{"task_id": str(i), "updated_at": i} for i in range(5)]
    page = paginate_tasks(rows, page=2, page_size=2)
    assert [r["task_id"] for r in page["items"]] == ["2", "3"]
    assert page["page"] == 2
    assert page["page_size"] == 2
    assert page["total"] == 5
    assert page["pages"] == 3


def test_chapter_actions_expose_only_safe_operations_for_terminal_state():
    assert task_actions("chapter", "running") == ["pause", "cancel"]
    assert task_actions("chapter", "paused") == ["resume", "cancel"]
    assert task_actions("chapter", "failed") == ["retry"]
    assert task_actions("chapter", "succeeded") == []
    assert task_actions("import", "analyzing") == ["cancel", "retry"]


def test_serializers_produce_common_shape_and_preserve_error_detail():
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=RUN_ID, book_id=BOOK_ID, chapter_id=CHAPTER_ID, chapter_no=7,
        status="retryable", current_step="drafting", control_requested="none",
        error_code="provider_timeout", error_detail={"message": "timeout"},
        started_at=now, finished_at=None, created_at=now, updated_at=now,
    )
    item = serialize_chapter_run(run)
    assert item["task_id"] == f"chapter:{RUN_ID}"
    assert item["task_type"] == "chapter"
    assert item["status"] == "retryable"
    assert item["error"]["code"] == "provider_timeout"
    assert "retry" in item["actions"]

    session = SimpleNamespace(
        id=RUN_ID, book_id=None, status="analyzing", progress=0.4,
        current_step="extracting", error_code=None, error_detail=None,
        created_at=now, updated_at=now,
    )
    assert serialize_import_session(session)["task_id"] == f"import:{RUN_ID}"

    research = SimpleNamespace(
        id=RUN_ID, book_id=BOOK_ID, chapter_id=None, status="running",
        requested_topic="history", created_at=now,
    )
    assert serialize_research_session(research)["task_type"] == "research"
