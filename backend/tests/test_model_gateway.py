"""Test model gateway — reasoning isolation, retry logic (pure functions)."""
from app.gateway.model_gateway import (
    StreamResult,
    AttemptRecord,
    _strip_inline_reasoning,
    _generation_controls,
    RETRYABLE_ERRORS,
    REASONING_FIELDS,
)


class TestGenerationControls:
    def test_glm_benchmark_can_disable_reasoning(self):
        assert _generation_controls(
            "z-ai/glm-5.2", max_tokens=512, reasoning_mode="disabled"
        ) == {
            "max_tokens": 512,
            "thinking": {"type": "disabled"},
        }

    def test_step_3_omits_truncating_output_cap(self):
        assert _generation_controls(
            "stepfun-ai/step-3.7-flash",
            max_tokens=512,
            reasoning_mode="disabled",
        ) == {}

    def test_unknown_model_keeps_standard_token_cap(self):
        assert _generation_controls(
            "some-unknown-model", max_tokens=512, reasoning_mode="disabled"
        ) == {"max_tokens": 512}

    def test_deepseek_reasoning_headroom(self):
        """DeepSeek-family reasoning shares the max_tokens budget with the
        final answer; the cap is raised so reasoning cannot consume it all."""
        assert _generation_controls(
            "deepseek-v4-flash", max_tokens=512, reasoning_mode="disabled"
        ) == {"max_tokens": 65536}


class TestStreamResult:
    def test_default_values(self):
        r = StreamResult()
        assert r.reasoning_text == ""
        assert r.final_content == ""
        assert r.reasoning_detected is False
        assert r.inline_leak_detected is False
        assert r.error is None
        assert r.actual_provider == ""
        assert r.actual_model == ""
        assert r.successful_attempt_no is None
        assert r.finish_reason is None
        assert r.attempts == []
        # backward-compat properties
        assert r.provider_used == "primary"
        assert r.attempt == 0

    def test_custom_values(self):
        r = StreamResult(
            final_content="Hello world",
            reasoning_text="Thinking...",
            reasoning_detected=True,
            actual_provider="new-api",
            actual_model="fallback-model",
            successful_attempt_no=3,
            provider_used="fallback",
            attempt=3,
        )
        assert r.final_content == "Hello world"
        assert r.reasoning_text == "Thinking..."
        assert r.actual_model == "fallback-model"
        assert r.successful_attempt_no == 3
        assert r.provider_used == "fallback"
        assert r.attempt == 3


class TestAttemptRecord:
    def test_fields(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        a = AttemptRecord(
            attempt_no=1,
            provider="p",
            model="m",
            route_type="primary",
            started_at=now,
            completed_at=now,
            latency_ms=5,
            success=False,
            error_code="HTTP_500",
            prompt_tokens=0,
            completion_tokens=0,
        )
        assert a.route_type == "primary"
        assert a.success is False


class TestReasoningFields:
    def test_fields_present(self):
        assert "reasoning_content" in REASONING_FIELDS
        assert "reasoning" in REASONING_FIELDS
        assert "thinking" in REASONING_FIELDS
        assert "thought" in REASONING_FIELDS


class TestRetryableErrors:
    def test_timeout_errors_retryable(self):
        assert "CONNECT_TIMEOUT" in RETRYABLE_ERRORS
        assert "READ_TIMEOUT" in RETRYABLE_ERRORS

    def test_http_errors_retryable(self):
        assert "HTTP_500" in RETRYABLE_ERRORS
        assert "HTTP_502" in RETRYABLE_ERRORS
        assert "HTTP_503" in RETRYABLE_ERRORS
        assert "HTTP_504" in RETRYABLE_ERRORS
        assert "HTTP_429" in RETRYABLE_ERRORS

    def test_empty_final_retryable(self):
        assert "final_content_empty" in RETRYABLE_ERRORS

    def test_unterminated_reasoning_retryable(self):
        assert "UNTERMINATED_REASONING" in RETRYABLE_ERRORS

    def test_non_retryable_not_in_set(self):
        assert "HTTP_400" not in RETRYABLE_ERRORS
        assert "HTTP_401" not in RETRYABLE_ERRORS
        assert "HTTP_403" not in RETRYABLE_ERRORS


class TestStripInlineReasoning:
    def test_clean_text_unchanged(self):
        text = "The knight entered the dark chamber."
        result, found = _strip_inline_reasoning(text)
        assert result == text
        assert found is False

    def test_reasoning_tags_stripped(self):
        text = "<reasoning>I should describe the scene</reasoning>The knight entered."
        result, found = _strip_inline_reasoning(text)
        assert "<reasoning>" not in result
        assert "The knight entered." in result
        assert found is True

    def test_thinking_tags_stripped(self):
        text = "<thinking>Let me plan this</thinking>She drew her sword."
        result, found = _strip_inline_reasoning(text)
        assert "<thinking>" not in result
        assert "She drew her sword." in result
        assert found is True

    def test_multiline_tags_stripped(self):
        text = "<reasoning>\nLine 1\nLine 2\n</reasoning>\nActual content here."
        result, found = _strip_inline_reasoning(text)
        assert "<reasoning>" not in result
        assert "Actual content here." in result
        assert found is True

    def test_multiple_tags_stripped(self):
        text = (
            "<reasoning>First</reasoning>"
            "Content A"
            "<thinking>Second</thinking>"
            "Content B"
        )
        result, found = _strip_inline_reasoning(text)
        assert "<reasoning>" not in result
        assert "<thinking>" not in result
        assert "Content A" in result
        assert "Content B" in result
        assert found is True
