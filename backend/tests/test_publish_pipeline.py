"""Test publish pipeline — normalization + leak guard (pure functions)."""
from app.gateway.publish_pipeline import full_pipeline, PublishState
from app.gateway.model_gateway import StreamResult


class TestPublishStateEnum:
    """Test PublishState enum values."""

    def test_all_states_present(self):
        assert PublishState.RAW_RECEIVED
        assert PublishState.DEMUXED
        assert PublishState.NORMALIZED
        assert PublishState.LEAK_CHECKED
        assert PublishState.PUBLISHABLE
        assert PublishState.BLOCKED

    def test_blocked_value(self):
        assert PublishState.BLOCKED.value == "blocked"

    def test_publishable_value(self):
        assert PublishState.PUBLISHABLE.value == "publishable"


class TestFullPipeline:
    """Test full_pipeline function with various StreamResult inputs."""

    def test_clean_prose_passes(self):
        """Clean prose content should reach PUBLISHABLE."""
        result = StreamResult(
            final_content="The old knight stood at the gate, his sword gleaming in the moonlight.",
            reasoning_text="",
        )
        publishable, state, meta = full_pipeline(result, is_json=False)
        assert state == PublishState.PUBLISHABLE
        assert publishable is not None
        assert "knight" in publishable

    def test_empty_final_blocked(self):
        """Empty final_content should be BLOCKED."""
        result = StreamResult(
            final_content="",
            error="final_content_empty",
        )
        publishable, state, meta = full_pipeline(result, is_json=False)
        assert state == PublishState.BLOCKED
        assert publishable is None
        assert meta["block_reason"] == "final_content_empty"

    def test_error_no_content_blocked(self):
        """Error with no content should be BLOCKED."""
        result = StreamResult(
            final_content="",
            error="HTTP_500",
        )
        publishable, state, meta = full_pipeline(result, is_json=False)
        assert state == PublishState.BLOCKED

    def test_json_output_parsed(self):
        """JSON output should be parsed into a dict."""
        json_str = '{"chapter_goal": "test", "scenes": []}'
        result = StreamResult(final_content=json_str)
        publishable, state, meta = full_pipeline(result, is_json=True)
        assert state == PublishState.PUBLISHABLE
        assert isinstance(publishable, dict)
        assert publishable["chapter_goal"] == "test"

    def test_json_with_markdown_fence(self):
        """JSON wrapped in markdown fence should still parse."""
        json_str = '```json\n{"goal": "test"}\n```'
        result = StreamResult(final_content=json_str)
        publishable, state, meta = full_pipeline(result, is_json=True)
        assert state == PublishState.PUBLISHABLE
        assert isinstance(publishable, dict)

    def test_leak_detected_blocked(self):
        """Content with high contamination should be BLOCKED."""
        # Create content where >10% is meta-commentary
        leaky_content = (
            "现在开始写正文内容。\n\n"
            "需要注意的是角色需要保持一致。\n\n"
            "作为AI我需要检查一下情节。\n\n"
            "接下来描写战斗场景。\n\n"
            "以下是正文内容。"
        )
        result = StreamResult(final_content=leaky_content)
        publishable, state, meta = full_pipeline(result, is_json=False)
        assert state == PublishState.BLOCKED
        assert meta.get("block_reason") == "leak_detected"

    def test_minor_leak_attached_as_warning(self):
        """Content with minor leak in a small paragraph should pass with warnings.
        
        Uses multiple paragraphs where only one small paragraph has a leak,
        keeping contamination ratio below 10%.
        """
        clean_paragraphs = (
            "The knight drew his sword and faced the dragon. "
            "The beast roared, shaking the very foundations of the castle. "
            "With a mighty leap, the knight charged forward, his blade "
            "gleaming in the firelight. The dragon breathed a torrent of "
            "flame, but the knight's shield held firm. It was a battle "
            "that would be remembered for ages.\n\n"
            "The kingdom was saved and peace returned to the land. "
            "People celebrated in the streets, singing songs of the "
            "heroic knight who had slain the fearsome beast. "
            "Stories were told for generations about the courage and "
            "valor displayed that day. The knight became a legend, "
            "his name whispered with reverence throughout the realm.\n\n"
            "Years later, a statue was erected in the town square, "
            "commemorating the historic battle. Children would gather "
            "around it, their eyes wide with wonder, as the elders "
            "recounted the tale of the knight who stood against "
            "the dragon and emerged victorious.\n\n"
            "Actually.\n\n"
            "The kingdom prospered under the knight's protection, "
            "and the dragon never returned to threaten the land again. "
            "Peace settled over the realm like a warm blanket, and the "
            "people lived happily, knowing they were safe."
        )
        result = StreamResult(final_content=clean_paragraphs)
        publishable, state, meta = full_pipeline(result, is_json=False)
        # "Actually" is in META_PATTERNS, but contamination ratio is low
        assert state == PublishState.PUBLISHABLE
        assert publishable is not None

    def test_reasoning_detected_flag_in_meta(self):
        """reasoning_detected flag should be in meta."""
        result = StreamResult(
            final_content="Clean content here.",
            reasoning_detected=True,
        )
        _, _, meta = full_pipeline(result, is_json=False)
        assert meta["reasoning_detected"] is True

    def test_invalid_json_blocked(self):
        """Invalid JSON should be BLOCKED."""
        result = StreamResult(final_content="not json at all {{{")
        publishable, state, meta = full_pipeline(result, is_json=True)
        assert state == PublishState.BLOCKED
        assert meta["block_reason"] == "json_parse_failed"

    def test_long_clean_prose_passes(self):
        """Long clean prose with multiple paragraphs should pass."""
        paragraphs = []
        for i in range(10):
            paragraphs.append(
                f"Chapter {i} began with a whisper in the dark. "
                f"The protagonist moved carefully through the shadows, "
                f"aware that danger lurked around every corner. "
                f"Each step was measured, each breath controlled. "
                f"The fate of the realm hung in the balance."
            )
        content = "\n\n".join(paragraphs)
        result = StreamResult(final_content=content)
        publishable, state, meta = full_pipeline(result, is_json=False)
        assert state == PublishState.PUBLISHABLE
        assert publishable is not None
