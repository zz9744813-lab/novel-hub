"""v9.3 PR-01: Editorial data layer tests (rubric, policy, grade, lineage)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.editorial.rubric import (
    DEFAULT_RUBRIC,
    RUBRIC_KEYS,
    RUBRIC_TOTAL,
    get_or_create_default_rubric,
    get_or_create_policy,
    resolve_rubric,
    score_to_grade,
    validate_rubric_scores,
)
from app.models import (
    EditorialAnnotation,
    EditorialExperienceCard,
    EditorialExperiment,
    EditorialFeedbackInsight,
    EditorialImprovementProposal,
    EditorialPreferencePair,
    EditorialRegressionCase,
    EditorialReviewPolicy,
    EditorialReviewRound,
    EditorialRubricTemplate,
)


class TestDefaultRubric:
    def test_default_rubric_weights_sum_to_100(self):
        assert RUBRIC_TOTAL == 100
        assert len(DEFAULT_RUBRIC) == 8

    def test_score_to_grade_bands(self):
        assert score_to_grade(95) == "A"
        assert score_to_grade(90) == "A"
        assert score_to_grade(89) == "B"
        assert score_to_grade(80) == "B"
        assert score_to_grade(79) == "C"
        assert score_to_grade(70) == "C"
        assert score_to_grade(69) == "D"
        assert score_to_grade(None) is None

    def test_validate_rubric_scores_caps_at_weight(self):
        total = validate_rubric_scores({"plot": 25, "character": 14}, DEFAULT_RUBRIC)
        assert total == 20 + 14  # 25 capped at weight 20

    def test_validate_rubric_scores_rejects_unknown_key(self):
        with pytest.raises(ValueError, match="UNKNOWN_RUBRIC_KEYS"):
            validate_rubric_scores({"plto": 10}, DEFAULT_RUBRIC)

    def test_validate_rubric_scores_rejects_negative(self):
        with pytest.raises(ValueError, match="INVALID_SCORE"):
            validate_rubric_scores({"plot": -1}, DEFAULT_RUBRIC)

    def test_full_marks_total_100(self):
        full = {d["key"]: d["weight"] for d in DEFAULT_RUBRIC}
        assert validate_rubric_scores(full, DEFAULT_RUBRIC) == 100


class FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class RubricSession:
    """Minimal session: returns canned rows for select(), collects adds."""

    def __init__(self):
        self.rows: dict = {}
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)
        # make idempotent lookups succeed after first add for rubric/policy
        if isinstance(obj, EditorialRubricTemplate):
            self.rows["rubric"] = obj
        if isinstance(obj, EditorialReviewPolicy):
            self.rows["policy"] = obj

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
        if "rubric" in self.rows and getattr(self.rows["rubric"], "id", None) is None:
            self.rows["rubric"].id = uuid.uuid4()
        if "policy" in self.rows and getattr(self.rows["policy"], "id", None) is None:
            self.rows["policy"].id = uuid.uuid4()

    async def execute(self, stmt):
        cols = getattr(stmt, "column_descriptions", [])
        entity = cols[0].get("entity") if cols else None
        if entity is EditorialRubricTemplate:
            return FakeResult(self.rows.get("rubric"))
        if entity is EditorialReviewPolicy:
            return FakeResult(self.rows.get("policy"))
        return FakeResult(None)


class TestPolicyBootstrap:
    def test_creates_default_policy_and_rubric_idempotently(self):
        import asyncio

        db = RubricSession()
        book_id = uuid.uuid4()
        p1 = asyncio.run(get_or_create_policy(db, book_id))
        assert p1.mode == "windowed"
        assert p1.max_unreviewed_ahead == 5
        assert p1.rubric_template_id is not None

        # second call returns same cached row, no duplicate add
        added_before = len(db.added)
        p2 = asyncio.run(get_or_create_policy(db, book_id))
        assert p2 is p1
        assert len(db.added) == added_before

    def test_resolve_rubric_falls_back_to_default(self):
        import asyncio

        db = RubricSession()
        dims = asyncio.run(resolve_rubric(db, uuid.uuid4()))
        assert [d["key"] for d in dims] == RUBRIC_KEYS


class TestModelShapes:
    """ORM models must expose the spec §14/§15/§33/§80/§81 fields."""

    def _cols(self, model) -> set[str]:
        return set(model.__table__.columns.keys())

    def test_review_round_fields(self):
        cols = self._cols(EditorialReviewRound)
        required = {
            "book_id", "chapter_id", "chapter_version_id", "round_no", "status",
            "verdict", "score_total", "grade", "rubric_template_id",
            "rubric_scores_json", "overall_comment", "reviewer_kind",
            "reviewer_id", "ai_issue_dispositions", "submitted_at", "completed_at",
        }
        assert required <= cols

    def test_annotation_composite_anchor_fields(self):
        cols = self._cols(EditorialAnnotation)
        required = {
            "review_round_id", "annotation_type", "category", "severity", "scope",
            "paragraph_key", "start_offset", "end_offset", "quoted_text",
            "quote_hash", "context_before", "context_after", "context_hash",
            "comment", "suggested_text", "is_blocking", "ai_issue_match_ids",
            "resolution_status", "resolved_by_version_id",
        }
        assert required <= cols

    def test_experience_card_fields(self):
        cols = self._cols(EditorialExperienceCard)
        required = {
            "rule_type", "scope_type", "scope_ref", "category",
            "trigger_conditions", "instruction", "rationale", "avoid_when",
            "target_components", "support_count", "contradiction_count",
            "confidence", "status", "is_locked", "effective_from_chapter",
            "source_annotation_ids",
        }
        assert required <= cols

    def test_preference_pair_uses_chosen_rejected(self):
        cols = self._cols(EditorialPreferencePair)
        assert {"rejected_text", "chosen_text", "source", "category", "scope"} <= cols

    def test_proposal_lifecycle_fields(self):
        cols = self._cols(EditorialImprovementProposal)
        required = {
            "proposal_type", "target_component", "risk_level", "candidate_patch",
            "supporting_experience_ids", "status", "approved_by", "approved_at",
            "experiment_id", "promoted_at", "rolled_back_at",
        }
        assert required <= cols

    def test_experiment_hard_gate_fields(self):
        cols = self._cols(EditorialExperiment)
        required = {
            "baseline_version", "candidate_version", "case_ids",
            "metrics_baseline", "metrics_candidate", "delta_metrics",
            "hard_gate_results", "pareto_candidates", "status", "recommendation",
        }
        assert required <= cols

    def test_chapter_version_lineage(self):
        from app.models import ChapterVersion

        cols = self._cols(ChapterVersion)
        assert {"parent_version_id", "editorial_review_round_id", "revision_origin"} <= cols

    def test_chapter_editorial_status_independent(self):
        from app.models import Chapter

        cols = self._cols(Chapter)
        assert "editorial_status" in cols
        assert "status" in cols

    def test_insight_one_to_one_annotation(self):
        cols = self._cols(EditorialFeedbackInsight)
        assert {"annotation_id", "normalized_category", "root_cause_component",
                "secondary_components", "remediation_level", "confidence",
                "evidence_refs", "analysis_run_id"} <= cols

    def test_regression_case_snapshot_fields(self):
        cols = self._cols(EditorialRegressionCase)
        assert {"source_review_round_id", "chapter_version_id", "case_type",
                "target_component", "context_package_refs", "prompt_version_ref",
                "model_binding_snapshot", "chapter_text", "human_verdict",
                "expected_properties", "forbidden_properties", "is_active"} <= cols
