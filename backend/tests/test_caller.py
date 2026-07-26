"""Test caller — short-lived session pattern (P0-02/P0-03).

call_agent no longer takes db; LLM is outside sessions; Run status is completed/failed.
"""
import inspect
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from app.agents.caller import call_agent
from app.gateway.model_gateway import StreamResult, AttemptRecord


def _attempt(success=True, error=None):
    now = datetime.now(timezone.utc)
    return AttemptRecord(
        attempt_no=1,
        provider="new-api",
        model="deepseek-v4-flash",
        route_type="primary",
        started_at=now,
        completed_at=now,
        latency_ms=10,
        success=success,
        error_code=error,
        prompt_tokens=1,
        completion_tokens=1,
    )


def _fake_run(status="completed"):
    fake_run = MagicMock()
    fake_run.id = uuid.uuid4()
    fake_run.book_id = uuid.uuid4()
    fake_run.chapter_id = None
    fake_run.scene_id = None
    fake_run.agent_role = "chapter_planner"
    fake_run.status = status
    fake_run.prompt_version = "v1"
    fake_run.model_name = "deepseek-v4-flash"
    fake_run.started_at = datetime.now(timezone.utc)
    fake_run.completed_at = datetime.now(timezone.utc)
    fake_run.idempotency_key = None
    fake_run.parent_run_id = None
    return fake_run


class TrackingSession:
    def __init__(self, status="completed"):
        self.added = []
        self.merged = []
        self._status = status
        self._events = None

    def bind_events(self, events):
        self._events = events

    async def __aenter__(self):
        if self._events is not None:
            self._events.append("session_open")
        return self

    async def __aexit__(self, *args):
        if self._events is not None:
            self._events.append("session_close")
        return False

    def add(self, obj):
        self.added.append(obj)

    async def merge(self, obj):
        # mutate status like real code path
        if hasattr(obj, "status"):
            obj.status = self._status
            obj.completed_at = datetime.now(timezone.utc)
            obj.model_name = "deepseek-v4-flash"
        self.merged.append(obj)
        return obj

    async def commit(self):
        pass

    async def flush(self):
        pass

    async def execute(self, *args, **kwargs):
        m = MagicMock()
        m.scalar_one.return_value = _fake_run(self._status)
        m.scalar_one_or_none.return_value = None
        return m

    async def refresh(self, *args, **kwargs):
        pass

    async def rollback(self):
        pass


class TestCallAgentSessionPattern:
    def test_imports_async_session_factory(self):
        import app.agents.caller as caller_mod
        assert hasattr(caller_mod, "async_session_factory")

    def test_call_agent_signature_no_db_param(self):
        sig = inspect.signature(call_agent)
        assert "db" not in sig.parameters
        assert "agent_role" in sig.parameters
        assert "user_content" in sig.parameters
        assert "book_id" in sig.parameters

    @pytest.mark.asyncio
    async def test_llm_call_outside_session(self):
        session_events = []
        session = TrackingSession("completed")
        session.bind_events(session_events)
        tracking_factory = MagicMock(return_value=session)

        mock_result = StreamResult(
            final_content='{"test": "output"}',
            reasoning_text="",
            reasoning_detected=False,
            prompt_tokens=100,
            completion_tokens=50,
            latency_ms=500,
            actual_provider="new-api",
            actual_model="deepseek-v4-flash",
            successful_attempt_no=1,
            attempts=[_attempt(True)],
            provider_used="primary",
            attempt=1,
        )

        with patch("app.agents.caller.async_session_factory", tracking_factory), \
             patch("app.agents.caller.stream_with_retry", new_callable=AsyncMock) as mock_stream, \
             patch("app.agents.caller.full_pipeline_async", new_callable=AsyncMock) as mock_pipeline, \
             patch("app.agents.caller._resolve_model", new_callable=AsyncMock) as mock_resolve:

            mock_resolve.return_value = ("new-api", "deepseek-v4-flash", None)
            mock_stream.return_value = mock_result
            mock_pipeline.return_value = ({"test": "output"}, MagicMock(value="publishable"), {})

            result = await call_agent(
                book_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                agent_role="chapter_planner",
                user_content='{"test": true}',
                chapter_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            )

            assert session_events.count("session_open") >= 2
            assert session_events.count("session_close") >= 2
            assert mock_stream.call_count == 1
            kwargs = mock_stream.call_args.kwargs
            assert kwargs.get("provider") == "new-api"
            assert kwargs.get("model") == "deepseek-v4-flash"
            run, publishable, meta = result
            assert run.status == "completed"

    @pytest.mark.asyncio
    async def test_failed_llm_still_saves_output(self):
        session = TrackingSession("failed")
        tracking_factory = MagicMock(return_value=session)

        mock_result = StreamResult(
            final_content="",
            error="HTTP_500",
            reasoning_text="some reasoning",
            reasoning_detected=True,
            actual_provider="new-api",
            actual_model="deepseek-v4-flash",
            attempts=[_attempt(False, "HTTP_500")],
            provider_used="primary",
            attempt=1,
        )

        with patch("app.agents.caller.async_session_factory", tracking_factory), \
             patch("app.agents.caller.stream_with_retry", new_callable=AsyncMock) as mock_stream, \
             patch("app.agents.caller.full_pipeline_async", new_callable=AsyncMock) as mock_pipeline, \
             patch("app.agents.caller._resolve_model", new_callable=AsyncMock) as mock_resolve:

            mock_resolve.return_value = ("new-api", "deepseek-v4-flash", None)
            mock_stream.return_value = mock_result
            mock_pipeline.return_value = (None, MagicMock(value="blocked"), {"block_reason": "HTTP_500"})

            run, publishable, meta = await call_agent(
                book_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
                agent_role="chapter_planner",
                user_content="test",
            )

            assert publishable is None
            assert run.status == "failed"
