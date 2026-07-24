"""Test retrieval engine — pure function tests (no DB required)."""
from app.engine.retrieval import (
    candidate_merge_and_score,
    deterministic_query_template,
    SCORE_WEIGHTS,
)


class TestScoreWeights:
    """Verify SCORE_WEIGHTS match §6.6 spec."""

    def test_required_dependency_weight(self):
        assert SCORE_WEIGHTS["required_dependency"] == 1000

    def test_human_locked_weight(self):
        assert SCORE_WEIGHTS["human_locked"] == 900

    def test_open_plot_thread_weight(self):
        assert SCORE_WEIGHTS["open_plot_thread"] == 700

    def test_character_overlap_weight(self):
        assert SCORE_WEIGHTS["character_overlap"] == 180

    def test_event_type_match_weight(self):
        assert SCORE_WEIGHTS["event_type_match"] == 120

    def test_full_text_max_weight(self):
        assert SCORE_WEIGHTS["full_text_max"] == 100

    def test_recency_max_weight(self):
        assert SCORE_WEIGHTS["recency_max"] == 20


class TestCandidateMergeAndScore:
    """Test merge + score logic."""

    def test_empty_inputs(self, sample_query_plan):
        result = candidate_merge_and_score([], [], sample_query_plan)
        assert result == []

    def test_event_with_character_overlap(self, sample_story_event, sample_query_plan):
        """Event whose subject_entity_ids overlap with query_plan character_ids gets +180."""
        result = candidate_merge_and_score([sample_story_event], [], sample_query_plan)
        assert len(result) == 1
        assert result[0]["rule_score"] >= SCORE_WEIGHTS["character_overlap"]
        assert "character_overlap" in result[0]["reasons"]

    def test_event_with_event_type_match(self, sample_query_plan):
        """Event whose event_type matches query_plan gets +120."""
        event = {
            "event_id": "evt-002",
            "event_type": "combat",
            "chapter_no": 3,
            "subject_entity_ids": [],
            "object_entity_ids": [],
        }
        result = candidate_merge_and_score([event], [], sample_query_plan)
        assert len(result) == 1
        assert result[0]["rule_score"] >= SCORE_WEIGHTS["event_type_match"]
        assert "event_type_match" in result[0]["reasons"]

    def test_ft_candidate_scored(self, sample_ft_candidate, sample_query_plan):
        """Full-text candidate gets score based on rank."""
        result = candidate_merge_and_score([], [sample_ft_candidate], sample_query_plan)
        assert len(result) == 1
        assert result[0]["rule_score"] > 0
        assert result[0]["source_type"] == "scene"

    def test_deduplication(self, sample_story_event, sample_query_plan):
        """Duplicate events are deduplicated."""
        result = candidate_merge_and_score(
            [sample_story_event, sample_story_event], [], sample_query_plan
        )
        assert len(result) == 1

    def test_sorted_by_score(self, sample_query_plan):
        """Results are sorted by score descending."""
        high_score_event = {
            "event_id": "evt-high",
            "event_type": "combat",
            "chapter_no": 8,
            "subject_entity_ids": ["char-001"],  # overlaps with query plan
            "object_entity_ids": [],
        }
        low_score_event = {
            "event_id": "evt-low",
            "event_type": "travel",
            "chapter_no": 1,
            "subject_entity_ids": ["char-999"],  # no overlap
            "object_entity_ids": [],
        }
        result = candidate_merge_and_score(
            [low_score_event, high_score_event], [], sample_query_plan
        )
        assert result[0]["event_id"] == "evt-high"
        assert result[0]["rule_score"] > result[1]["rule_score"]

    def test_max_24_results(self, sample_query_plan):
        """Only top 24 candidates are returned."""
        events = []
        for i in range(30):
            events.append({
                "event_id": f"evt-{i:03d}",
                "event_type": "combat",
                "chapter_no": i,
                "subject_entity_ids": ["char-001"],
                "object_entity_ids": [],
            })
        result = candidate_merge_and_score(events, [], sample_query_plan)
        assert len(result) <= 24

    def test_recency_tiebreaker(self, sample_query_plan):
        """More recent events get higher recency score."""
        recent = {
            "event_id": "evt-recent",
            "event_type": "other",
            "chapter_no": 9,  # close to chapter_range.to=10
            "subject_entity_ids": [],
            "object_entity_ids": [],
        }
        old = {
            "event_id": "evt-old",
            "event_type": "other",
            "chapter_no": 1,  # far from chapter_range.to=10
            "subject_entity_ids": [],
            "object_entity_ids": [],
        }
        result = candidate_merge_and_score([recent, old], [], sample_query_plan)
        recent_score = next(r["rule_score"] for r in result if r["event_id"] == "evt-recent")
        old_score = next(r["rule_score"] for r in result if r["event_id"] == "evt-old")
        assert recent_score > old_score

    def test_chapter_no_in_event_results(self, sample_story_event, sample_query_plan):
        """Verify chapter_no is present in merged results (P1 fix)."""
        result = candidate_merge_and_score([sample_story_event], [], sample_query_plan)
        assert "chapter_no" in result[0]
        assert result[0]["chapter_no"] == 5


class TestDeterministicQueryTemplate:
    """Test fallback query plan generation."""

    def test_basic_structure(self):
        plan = deterministic_query_template(
            outline_node={
                "involved_character_ids": ["char-001"],
                "plot_thread_ids": ["thread-001"],
            },
            scene_plan={},
            required_deps=[],
            l4_st={},
            current_chapter=5,
        )
        assert "character_ids" in plan
        assert "event_types" in plan
        assert "chapter_range" in plan
        assert plan["chapter_range"]["to"] == 4  # current - 1

    def test_includes_required_deps(self):
        deps = [{"target_node_id": "node-001", "required": True}]
        plan = deterministic_query_template(
            outline_node={"involved_character_ids": [], "plot_thread_ids": []},
            scene_plan={},
            required_deps=deps,
            l4_st={},
            current_chapter=3,
        )
        assert "node-001" in plan["required_outline_node_ids"]
