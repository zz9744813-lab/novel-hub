"""Test InlineReasoningParser — cross-chunk tag handling per §11.4."""
from app.gateway.provider_adapter import (
    InlineReasoningParser,
    CanonicalEventType,
    THINK_OPEN,
    THINK_CLOSE,
    classify_delta,
)


class TestThinkTagConstants:
    """Verify thinking tag constants are correct."""

    def test_think_open_length(self):
        assert len(THINK_OPEN) == 7

    def test_think_close_length(self):
        assert len(THINK_CLOSE) == 8

    def test_think_open_content(self):
        assert THINK_OPEN == chr(60) + "think" + chr(62)

    def test_think_close_content(self):
        assert THINK_CLOSE == chr(60) + "/think" + chr(62)


class TestInlineReasoningParserClean:
    """Test parser with clean content (no thinking tags)."""

    def test_single_chunk_clean(self):
        parser = InlineReasoningParser()
        events = parser.feed("Hello world, this is clean content.")
        assert len(events) == 1
        assert events[0][0] == CanonicalEventType.FINAL
        assert "Hello world" in events[0][1]

    def test_multiple_chunks_clean(self):
        parser = InlineReasoningParser()
        all_text = ""
        events = parser.feed("First part. ")
        events += parser.feed("Second part. ")
        events += parser.feed("Third part.")
        final_text = "".join(t for evt, t in events if evt == CanonicalEventType.FINAL)
        assert "First part" in final_text
        assert "Second part" in final_text
        assert "Third part" in final_text

    def test_flush_with_leftover_carry(self):
        """Flush should emit any remaining carry as FINAL."""
        parser = InlineReasoningParser()
        # Simulate leftover carry (e.g. content buffered waiting for more chunks)
        parser.carry = "leftover content"
        events = parser.flush()
        assert len(events) == 1
        assert events[0][0] == CanonicalEventType.FINAL
        assert "leftover content" in events[0][1]

    def test_flush_empty(self):
        """Flush with no carry should return empty list."""
        parser = InlineReasoningParser()
        events = parser.flush()
        assert len(events) == 0

    def test_feed_then_flush_clean(self):
        """feed + flush should return all content as FINAL."""
        parser = InlineReasoningParser()
        events = parser.feed("Some content")
        events += parser.flush()
        # All content should be returned as FINAL
        final_parts = [t for evt, t in events if evt == CanonicalEventType.FINAL]
        assert "Some content" in "".join(final_parts)


class TestInlineReasoningParserWithTags:
    """Test parser with thinking tags in content."""

    def test_single_think_block(self):
        parser = InlineReasoningParser()
        content = "Before" + THINK_OPEN + "internal thought" + THINK_CLOSE + "After"
        events = parser.feed(content)
        events += parser.flush()

        final_parts = [t for evt, t in events if evt == CanonicalEventType.FINAL]
        reasoning_parts = [t for evt, t in events if evt == CanonicalEventType.REASONING]

        final_text = "".join(final_parts)
        assert "Before" in final_text
        assert "After" in final_text
        assert THINK_OPEN not in final_text
        assert THINK_CLOSE not in final_text
        assert len(reasoning_parts) > 0
        assert "internal thought" in "".join(reasoning_parts)

    def test_think_block_split_across_chunks(self):
        """Tag split across chunk boundaries should still be detected."""
        parser = InlineReasoningParser()
        full = "Text" + THINK_OPEN + "reasoning" + THINK_CLOSE + "More"
        mid = len(full) // 2

        events = parser.feed(full[:mid])
        events += parser.feed(full[mid:])
        events += parser.flush()

        final_parts = [t for evt, t in events if evt == CanonicalEventType.FINAL]
        reasoning_parts = [t for evt, t in events if evt == CanonicalEventType.REASONING]

        final_text = "".join(final_parts)
        assert "Text" in final_text
        assert "More" in final_text
        assert THINK_OPEN not in final_text
        assert THINK_CLOSE not in final_text

    def test_unterminated_think_tag(self):
        """Unterminated thinking tag should produce UNKNOWN on flush."""
        parser = InlineReasoningParser()
        content = "Text" + THINK_OPEN + "unterminated reasoning"
        events = parser.feed(content)
        events += parser.flush()

        # Should have FINAL for "Text" and UNKNOWN for the unterminated part
        types = [evt for evt, _ in events]
        assert CanonicalEventType.FINAL in types
        assert CanonicalEventType.UNKNOWN in types

    def test_multiple_think_blocks(self):
        """Multiple thinking blocks in one content."""
        parser = InlineReasoningParser()
        content = (
            "Start" + THINK_OPEN + "thought1" + THINK_CLOSE
            + "Middle" + THINK_OPEN + "thought2" + THINK_CLOSE + "End"
        )
        events = parser.feed(content)
        events += parser.flush()

        final_parts = [t for evt, t in events if evt == CanonicalEventType.FINAL]
        reasoning_parts = [t for evt, t in events if evt == CanonicalEventType.REASONING]

        final_text = "".join(final_parts)
        assert "Start" in final_text
        assert "Middle" in final_text
        assert "End" in final_text
        reasoning_text = "".join(reasoning_parts)
        assert "thought1" in reasoning_text
        assert "thought2" in reasoning_text

    def test_empty_think_block(self):
        """Empty thinking block should be handled."""
        parser = InlineReasoningParser()
        content = "Before" + THINK_OPEN + THINK_CLOSE + "After"
        events = parser.feed(content)
        events += parser.flush()

        final_parts = [t for evt, t in events if evt == CanonicalEventType.FINAL]
        final_text = "".join(final_parts)
        assert "Before" in final_text
        assert "After" in final_text


class TestClassifyDelta:
    """Test classify_delta function."""

    def test_content_classified_as_final(self):
        evt, text = classify_delta({"content": "hello"})
        assert evt == CanonicalEventType.FINAL
        assert text == "hello"

    def test_reasoning_content_classified(self):
        evt, text = classify_delta({"reasoning_content": "thinking"})
        assert evt == CanonicalEventType.REASONING
        assert text == "thinking"

    def test_reasoning_field_classified(self):
        evt, text = classify_delta({"reasoning": "thought"})
        assert evt == CanonicalEventType.REASONING

    def test_thinking_field_classified(self):
        evt, text = classify_delta({"thinking": "internal"})
        assert evt == CanonicalEventType.REASONING

    def test_tool_calls_classified(self):
        evt, text = classify_delta({"tool_calls": [{"id": "1"}]})
        assert evt == CanonicalEventType.TOOL

    def test_empty_delta_classified_as_unknown(self):
        evt, text = classify_delta({})
        assert evt == CanonicalEventType.UNKNOWN
