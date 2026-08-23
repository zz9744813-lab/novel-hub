"""v9.3 PR-09/10/11: Improvement proposals + experiments tests."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.editorial.improvement import (
    apply_substitutions,
    generate_gepa_candidates,
    pareto_front,
    promote_proposal,
    review_proposal,
    rollback_proposal,
    run_experiment,
    run_hard_gates,
)
from app.models.tables import (
    EditorialExperienceCard,
    EditorialExperiment,
    EditorialImprovementProposal,
    EditorialRegressionCase,
)

BOOK_ID = uuid.uuid4()


class FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        if isinstance(self._row, list):
            return self._row
        return [self._row] if self._row is not None else []

    def __iter__(self):
        return iter(self.all())


class FakeSession:
    def __init__(self, store: dict):
        self.store = store
        self.commits = 0

    async def execute(self, stmt):
        cols = getattr(stmt, "column_descriptions", [])
        entity = cols[0].get("entity") if cols else None
        wc = getattr(stmt, "whereclause", None)
        vals: dict = {}
        if wc is not None:
            for crit in list(getattr(wc, "clauses", None) or [wc]):
                name = getattr(getattr(crit, "left", None), "name", None)
                if name:
                    vals[name] = getattr(getattr(crit, "right", None), "value", None)

        if entity is EditorialImprovementProposal:
            for p in self.store.get("proposals", []):
                if str(p.id) == str(vals.get("id")):
                    return FakeResult(p)
            return FakeResult(None)
        if entity is EditorialRegressionCase:
            matched = [c for c in self.store.get("cases", []) if str(c.book_id) == str(vals.get("book_id"))]
            if vals.get("is_active") is True:
                matched = [c for c in matched if c.is_active]
            return FakeResult(matched)
        if entity is EditorialExperienceCard:
            matched = [
                c for c in self.store.get("cards", [])
                if str(c.book_id) == str(vals.get("book_id")) and c.status == "active"
            ]
            return FakeResult(matched)
        return FakeResult(None)

    def add(self, obj):
        if isinstance(obj, EditorialExperiment):
            self.store.setdefault("experiments", []).append(obj)

    async def flush(self):
        for e in self.store.get("experiments", []):
            if getattr(e, "id", None) is None:
                e.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _proposal(status="proposed", **kw):
    base = dict(
        id=uuid.uuid4(),
        book_id=BOOK_ID,
        proposal_type="editorial_feedback_batch",
        target_component="draft_writer",
        target_scope="book",
        current_version_ref=None,
        candidate_patch={},
        risk_level="low",
        reason=None,
        supporting_experience_ids=[],
        supporting_review_ids=[],
        status=status,
        created_by_run_id=None,
        approved_by=None,
        approved_at=None,
        experiment_id=None,
        promoted_at=None,
        effective_from_chapter=None,
        rolled_back_at=None,
    )
    base.update(kw)
    return EditorialImprovementProposal(**base)


def _case(text="好的正文。", forbidden=None, active=True):
    return EditorialRegressionCase(
        id=uuid.uuid4(),
        book_id=BOOK_ID,
        source_review_round_id=None,
        chapter_version_id=uuid.uuid4(),
        scene_no=None,
        case_type="chapter_review",
        target_component="review_agent",
        context_package_refs=[],
        prompt_version_ref=None,
        model_binding_snapshot=None,
        scene_contract_ref=None,
        style_contract_ref=None,
        chapter_text=text,
        human_verdict="accept",
        rubric_scores=None,
        human_annotation_ids=[],
        expected_properties=[],
        forbidden_properties=forbidden or [],
        difficulty="normal",
        scene_type=None,
        is_active=active,
    )


def _anti_card(instruction="禁止出现：瞬间移动、灵力外挂"):
    return EditorialExperienceCard(
        id=uuid.uuid4(),
        book_id=BOOK_ID,
        rule_type="anti_pattern",
        scope_type="book",
        scope_ref={},
        category="plot",
        trigger_conditions={},
        instruction=instruction,
        rationale=None,
        avoid_when=[],
        target_components=["draft_writer"],
        positive_example_refs=[],
        negative_example_refs=[],
        support_count=2,
        contradiction_count=0,
        confidence=0.7,
        status="active",
        is_locked=False,
        effective_from_chapter=None,
        last_confirmed_at=None,
        source_annotation_ids=[],
    )


def _store(**kw):
    base = {"proposals": [], "cases": [], "cards": [], "experiments": []}
    base.update(kw)
    return base


class TestHardGates:
    def test_all_pass(self):
        case = _case(text="干净正文", forbidden=[])
        gates = run_hard_gates([case], {str(case.id): "干净正文"}, [_anti_card()])
        assert gates["hard_pass"] is True
        assert gates["passed"] == 1

    def test_forbidden_keyword_fails_closed(self):
        case = _case(forbidden=["瞬间移动"])
        gates = run_hard_gates([case], {str(case.id): "他使用了瞬间移动逃走"}, [])
        assert gates["hard_pass"] is False
        assert gates["cases"][0]["forbidden_clean"] is False

    def test_missing_candidate_text_fails(self):
        case = _case()
        gates = run_hard_gates([case], {}, [])
        assert gates["hard_pass"] is False
        assert gates["cases"][0]["nonempty"] is False

    def test_anti_pattern_card_violation_detected(self):
        case = _case()
        gates = run_hard_gates([case], {str(case.id): "他开了灵力外挂"}, [_anti_card()])
        assert gates["hard_pass"] is False
        assert gates["cases"][0]["card_violations"]


class TestProposalLifecycle:
    def test_approve_sets_reviewer_and_status(self):
        p = _proposal()
        db = FakeSession(_store(proposals=[p]))
        got = asyncio.run(review_proposal(db, p.id, True, reviewer="editor-1"))
        assert got.status == "approved"
        assert got.approved_by == "editor-1"
        assert got.approved_at is not None

    def test_reject(self):
        p = _proposal()
        db = FakeSession(_store(proposals=[p]))
        got = asyncio.run(review_proposal(db, p.id, False))
        assert got.status == "rejected"

    def test_terminal_status_rejected_by_gate(self):
        p = _proposal(status="promoted")
        db = FakeSession(_store(proposals=[p]))
        with pytest.raises(ValueError, match="INVALID_PROPOSAL_STATUS"):
            asyncio.run(review_proposal(db, p.id, True))

    def test_promote_requires_approved(self):
        p = _proposal(status="proposed")
        db = FakeSession(_store(proposals=[p]))
        with pytest.raises(ValueError, match="INVALID_PROMOTION_STATUS"):
            asyncio.run(promote_proposal(db, p.id, 40))

    def test_promote_and_rollback(self):
        p = _proposal(status="approved")
        db = FakeSession(_store(proposals=[p]))
        got = asyncio.run(promote_proposal(db, p.id, 40))
        assert got.status == "promoted"
        assert got.effective_from_chapter == 40
        got = asyncio.run(rollback_proposal(db, p.id))
        assert got.status == "rolled_back"
        assert got.rolled_back_at is not None

    def test_rollback_only_from_promoted(self):
        p = _proposal(status="approved")
        db = FakeSession(_store(proposals=[p]))
        with pytest.raises(ValueError, match="INVALID_ROLLBACK_STATUS"):
            asyncio.run(rollback_proposal(db, p.id))


class TestExperiment:
    def test_offline_replay_completes_with_recommendation(self):
        case = _case(text="干净的正文文本")
        store = _store(cases=[case])
        exp = asyncio.run(run_experiment(FakeSession(store), BOOK_ID))
        assert exp is not None
        assert exp.status == "completed"
        assert exp.hard_gate_results["hard_pass"] is True
        assert exp.recommendation in {"promote", "hold"}
        assert exp.metrics_candidate["total"] == 1

    def test_no_cases_returns_none(self):
        assert asyncio.run(run_experiment(FakeSession(_store()), BOOK_ID)) is None

    def test_failing_candidate_recommends_hold(self):
        case = _case(forbidden=["老套桥段"])
        store = _store(cases=[case])
        # candidate text violates the forbidden property
        exp = asyncio.run(
            run_experiment(FakeSession(store), BOOK_ID, candidate_texts={str(case.id): "又是老套桥段"})
        )
        assert exp.hard_gate_results["hard_pass"] is False
        assert exp.recommendation == "hold"

    def test_passing_candidate_recommends_promote(self):
        case = _case()
        store = _store(cases=[case])
        exp = asyncio.run(
            run_experiment(FakeSession(store), BOOK_ID, candidate_texts={str(case.id): "优秀候选文本"})
        )
        assert exp.recommendation == "promote"


class TestGEPA:
    def test_generate_candidates_includes_identity_and_cards(self):
        cands = generate_gepa_candidates([_anti_card()])
        names = [c["name"] for c in cands]
        assert "identity" in names
        assert any(n.startswith("card:") for n in names)

    def test_proposal_patch_becomes_candidate(self):
        p = _proposal(candidate_patch={"substitutions": [{"find": "老套", "replace": "新颖"}]})
        cands = generate_gepa_candidates([], p)
        assert any(c["source"] == "proposal_patch" for c in cands)

    def test_empty_proposal_patch_skipped(self):
        cands = generate_gepa_candidates([], _proposal(candidate_patch={}))
        assert [c["name"] for c in cands] == ["identity"]

    def test_apply_substitutions_deletes_fragment(self):
        assert apply_substitutions("他开了灵力外挂", [{"find": "灵力外挂", "replace": ""}]) == "他开了"

    def test_pareto_front_keeps_nondominated(self):
        evals = [
            {"name": "a", "pass_rate": 100.0, "retention": 0.8},
            {"name": "b", "pass_rate": 50.0, "retention": 1.0},
            {"name": "c", "pass_rate": 50.0, "retention": 0.8},
        ]
        front = pareto_front(evals)
        assert {e["name"] for e in front} == {"a", "b"}
        assert evals[2]["pareto_rank"] == 1
        assert evals[0]["pareto_rank"] == 0

    def test_gepa_experiment_repairs_violations_and_promotes(self):
        case = _case(text="他开了灵力外挂然后转身离开")
        card = _anti_card()
        store = _store(cases=[case], cards=[card])
        exp = asyncio.run(run_experiment(FakeSession(store), BOOK_ID, use_gepa=True))
        assert exp.candidate_version == "gepa"
        assert exp.hard_gate_results["hard_pass"] is True
        assert exp.pareto_candidates
        assert exp.recommendation == "promote"
        assert exp.metrics_candidate["passed"] == 1

    def test_gepa_prefers_card_candidate_over_identity(self):
        case = _case(text="他开了灵力外挂然后转身离开")
        store = _store(cases=[case], cards=[_anti_card()])
        exp = asyncio.run(run_experiment(FakeSession(store), BOOK_ID, use_gepa=True))
        best_names = [c["name"] for c in exp.pareto_candidates if c["pareto_rank"] == 0]
        assert any(n.startswith("card:") for n in best_names)
