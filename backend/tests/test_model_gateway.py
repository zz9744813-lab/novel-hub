"""Test model gateway — reasoning isolation, retry logic (pure functions)."""
from app.gateway.model_gateway import (
    StreamResult,
    _strip_inline_reasoning,
    RETRYABLE_ERRORS,
    REASONING_FIELDS,
)


class TestStreamResult:
    """Test StreamResult dataclass."""

    def test_default_values(self):
        r = StreamResult()
        assert r.reasoning_text == ""
        assert r.final_content == ""
        assert r.reasoning_detected is False
        assert r.inline_leak_detected is False
        assert r.error is None
        assert r.provider_used == "primary"
        assert r.attempt == 0

    def test_custom_values(self):
        r = StreamResult(
            final_content="Hello world",
            reasoning_text="Thinking...",
            reasoning_detected=True,
            provider_used="fallback",
            attempt=3,
        )
        assert r.final_content == "Hello world"
        assert r.reasoning_text == "Thinking..."
        assert r.provider_used == "fallback"
        assert r.attempt == 3


class TestReasoningFields:
    """Test reasoning field whitelist per C-18."""

    def test_fields_present(self):
        assert "reasoning_content" in REASONING_FIELDS
        assert "reasoning" in REASONING_FIELDS
        assert "thinking" in REASONING_FIELDS
        assert "thought" in REASONING_FIELDS


class TestRetryableErrors:
    """Test retryable error set per §11.11."""

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
        """final_content_empty is retryable (will try fallback)."""
        assert "final_content_empty" in RETRYABLE_ERRORS

    def test_unterminated_reasoning_retryable(self):
        assert "UNTERMINATED_REASONING" in RETRYABLE_ERRORS

    def test_non_retryable_not_in_set(self):
        """Non-retryable errors should NOT be in the set."""
        assert "HTTP_400" not in RETRYABLE_ERRORS
        assert "HTTP_401" not in RETRYABLE_ERRORS
        assert "HTTP_403" not in RETRYABLE_ERRORS


class TestStripInlineReasoning:
    """Test inline reasoning tag stripping."""

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
