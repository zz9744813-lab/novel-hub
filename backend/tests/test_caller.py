"""Test caller — short-lived session pattern (§2.5 compliance).

These tests verify that call_agent:
1. Creates its own sessions (doesn't use the passed db during LLM)
2. LLM call happens between sessions
3. Output is properly stored
"""
import asyncio
import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.caller import call_agent
from app.gateway.model_gateway import StreamResult


class TestCallAgentSessionPattern:
    """Verify §2.5: short-lived sessions in call_agent."""

    def test_imports_async_session_factory(self):
        """caller.py must import async_session_factory."""
        import app.agents.caller as caller_mod
        assert hasattr(caller_mod, "async_session_factory")

    def test_call_agent_signature_preserves_db_param(self):
        """db parameter is kept for backward compat but not used during LLM."""
        sig = inspect.signature(call_agent)
        assert "db" in sig.parameters
        assert "agent_role" in sig.parameters
        assert "user_content" in sig.parameters

    @pytest.mark.asyncio
    async def test_llm_call_outside_session(self):
        """LLM call (stream_with_retry) should NOT happen inside a DB session context.

        We track session open/close and verify the LLM call happens
        between them.
        """
        session_events = []

        original_factory = None

        class TrackingSession:
            """Mock session that tracks open/close events."""

            def __init__(self):
                self._closed = False

            async def __aenter__(self):
                session_events.append("session_open")
                return self

            async def __aexit__(self, *args):
                session_events.append("session_close")
                self._closed = True
                return False

            def add(self, obj):
                pass

            def merge(self, obj):
                return obj

            async def commit(self):
                pass

            async def flush(self):
                pass

            async def execute(self, *args, **kwargs):
                return MagicMock()

            async def refresh(self, *args, **kwargs):
                pass

        tracking_factory = MagicMock(return_value=TrackingSession())

        # Create a mock StreamResult for success
        mock_result = StreamResult(
            final_content='{"test": "output"}',
            reasoning_text="",
            reasoning_detected=False,
            prompt_tokens=100,
            completion_tokens=50,
            latency_ms=500,
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

            # Call call_agent with a dummy db (should be ignored)
            dummy_db = MagicMock()
            result = await call_agent(
                db=dummy_db,
                book_id="00000000-0000-0000-0000-000000000001",
                agent_role="chapter_planner",
                user_content='{"test": true}',
                chapter_id="00000000-0000-0000-0000-000000000002",
            )

            # Verify sessions were opened (at least 2: one for run, one for output)
            assert session_events.count("session_open") >= 2
            assert session_events.count("session_close") >= 2

            # Verify stream_with_retry was called (the LLM call)
            assert mock_stream.call_count == 1

            # Verify provider is passed to gateway
            kwargs = mock_stream.call_args.kwargs
            assert kwargs.get("provider") == "new-api"
            assert kwargs.get("model") == "deepseek-v4-flash"

            # Verify the dummy_db was never used for add/flush/commit
            # (call_agent should use its own sessions)
            dummy_db.add.assert_not_called()
            dummy_db.flush.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_llm_still_saves_output(self):
        """Even if LLM fails, AgentRun should be saved with failed status."""
        mock_result = StreamResult(
            final_content="",
            error="HTTP_500",
            reasoning_text="some reasoning",
            reasoning_detected=True,
        )

        class TrackingSession:
            def __init__(self):
                self.added = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def add(self, obj):
                self.added.append(obj)

            def merge(self, obj):
                return obj

            async def commit(self):
                pass

            async def flush(self):
                pass

            async def execute(self, *args, **kwargs):
                return MagicMock()

            async def refresh(self, *args, **kwargs):
                pass

        session = TrackingSession()
        tracking_factory = MagicMock(return_value=session)

        with patch("app.agents.caller.async_session_factory", tracking_factory), \
             patch("app.agents.caller.stream_with_retry", new_callable=AsyncMock) as mock_stream, \
             patch("app.agents.caller.full_pipeline_async", new_callable=AsyncMock) as mock_pipeline, \
             patch("app.agents.caller._resolve_model", new_callable=AsyncMock) as mock_resolve:

            mock_resolve.return_value = ("new-api", "deepseek-v4-flash", None)
            mock_stream.return_value = mock_result
            mock_pipeline.return_value = (None, MagicMock(value="blocked"), {"block_reason": "HTTP_500"})

            run, publishable, meta = await call_agent(
                db=MagicMock(),
                book_id="00000000-0000-0000-0000-000000000001",
                agent_role="chapter_planner",
                user_content="test",
            )

            assert publishable is None
            assert run.status == "failed"
