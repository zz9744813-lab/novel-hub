"""Regression tests for production writing-session recovery paths."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest


@pytest.mark.asyncio
async def test_failed_chapter_is_requeued_before_pipeline_retry(monkeypatch):
    import app.workers.arq_worker as worker

    transition = AsyncMock()
    monkeypatch.setattr(worker, "transition_chapter", transition)
    run_id = uuid.uuid4()
    chapter = SimpleNamespace(id=uuid.uuid4(), status="failed")

    changed = await worker._reset_chapter_for_retry(object(), chapter, run_id)

    assert changed is True
    transition.assert_awaited_once()
    assert transition.await_args.args[1].value == "queued"
    assert transition.await_args.kwargs["expected_states"] == {
        "failed",
        "resource_blocked",
    }
    assert transition.await_args.kwargs["run_id"] == run_id


@pytest.mark.asyncio
async def test_unhandled_pipeline_exception_terminalizes_run_and_wakes_session(monkeypatch):
    import app.workers.arq_worker as worker

    session_id = uuid.uuid4()
    run = SimpleNamespace(
        id=uuid.uuid4(),
        writing_session_id=session_id,
        status="running",
        error_code=None,
        error_detail=None,
        finished_at=None,
        lease_owner="worker-a",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )

    class Result:
        def scalar_one_or_none(self):
            return run

    class Db:
        async def execute(self, _statement):
            return Result()

    transition = AsyncMock()
    wake_session = AsyncMock()
    monkeypatch.setattr(worker, "transition_chapter", transition)
    monkeypatch.setattr(worker, "_insert_session_advance_outbox", wake_session)

    error = RuntimeError("boom")
    await worker._record_pipeline_exception(
        Db(),
        chapter_id=uuid.uuid4(),
        chapter_no=1,
        task_row_id=None,
        chapter_run_id=run.id,
        error=error,
    )

    assert run.status == "failed"
    assert run.error_code == "pipeline_error"
    assert run.error_detail == {"type": "RuntimeError", "message": "boom"}
    assert run.finished_at is not None
    assert run.lease_owner is None
    assert run.lease_expires_at is None
    wake_session.assert_awaited_once_with(
        ANY,
        session_id,
        run.id,
    )


def test_stale_running_chapter_run_requires_missing_or_expired_lease():
    from app.services.session_reconciler import chapter_run_is_stale

    now = datetime.now(timezone.utc)
    stale = SimpleNamespace(
        status="running",
        lease_owner=None,
        lease_expires_at=None,
        heartbeat_at=now - timedelta(minutes=10),
        started_at=now - timedelta(minutes=11),
        updated_at=now - timedelta(minutes=10),
        created_at=now - timedelta(minutes=12),
    )
    live = SimpleNamespace(
        status="running",
        lease_owner="worker-a",
        lease_expires_at=now + timedelta(seconds=60),
        heartbeat_at=now - timedelta(minutes=10),
        started_at=now - timedelta(minutes=11),
        updated_at=now - timedelta(minutes=10),
        created_at=now - timedelta(minutes=12),
    )

    assert chapter_run_is_stale(stale, now) is True
    assert chapter_run_is_stale(live, now) is False
