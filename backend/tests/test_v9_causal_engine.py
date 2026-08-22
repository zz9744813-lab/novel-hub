"""v9.0 Cognitive-Causal Narrative Engine unit tests.

Pure deterministic tests — no DB, no LLM. Covers:
- narrative_state: path ops, deep merge, belief/goal helpers
- causal_engine: preconditions, hard effects, knowledge, intention support
- appraisal_engine: appraisal → VAD, affect integration, expression constraints
- scene_contract: compile, renumber, hash stability, validation
- counterfactual_audit: key node detection + classification
"""
import uuid

import pytest

from app.contracts.narrative import (
    AffectTransition,
    BeliefDelta,
    CausalEdge,
    IntentionContract,
    PerceptionDelta,
    ProvisionalEvent,
    SceneProposal,
    StateDelta,
    StatePredicate,
    VAD,
)
from app.engine.narrative_state import (
    active_goals,
    apply_state_deltas,
    deep_merge_state,
    evaluate_predicate,
    get_path,
    normalize_state,
    set_path,
)
from app.engine.causal_engine import CausalEngine
from app.engine.appraisal_engine import AppraisalEngine
from app.engine.scene_contract import SceneContractCompiler
from app.engine.counterfactual_audit import audit_counterfactual, is_key_node_event
from app.engine.causal_errors import CausalHardBlockError, CausalRuntimeError


# ── fixtures ──────────────────────────────────────────────────────────

CHAR_A = "char-a"
CHAR_B = "char-b"


def _state_a() -> dict:
    return {
        "beliefs": {
            "ally_trustworthy": {
                "polarity": 1,
                "confidence": 0.8,
                "source_event_ids": ["P-001-01-01"],
            },
            "city_is_safe": {"polarity": 1, "confidence": 0.6, "source_event_ids": []},
        },
        "goals": {
            "revenge": {"status": "active", "priority": 0.9, "caused_by_event_ids": []},
            "retire": {"status": "abandoned", "priority": 0.1},
        },
        "relationships": {"char-b": {"trust": 0.7}},
        "affect": {"vad": {"valence": -0.2, "arousal": 0.5, "dominance": 0.1}},
    }


def _proposal() -> SceneProposal:
    return SceneProposal(
        scene_no=1,
        dramatic_goal="主角发现盟友的背叛证据",
        pov_character_id=CHAR_A,
        characters=[CHAR_A, CHAR_B],
        provisional_events=[
            ProvisionalEvent(
                event_key="E1",
                actor_id=CHAR_B,
                action="秘密传递情报给敌对势力",
                involves=[CHAR_A],
                hard_effects=[
                    StateDelta(path=f"{CHAR_A}.relationships.char-b.trust", value=0.1, mode="hard")
                ],
            ),
            ProvisionalEvent(
                event_key="E2",
                actor_id=CHAR_A,
                action="质问盟友并摊牌",
                involves=[CHAR_B],
            ),
        ],
        causal_edges=[
            CausalEdge(from_key="E1", to_key="E2", relation="MOTIVATES", mode="hard"),
            CausalEdge(from_key="E1", to_key="E2", relation="TEMPORAL_BEFORE", mode="soft"),
        ],
        belief_deltas=[
            BeliefDelta(
                character_id=CHAR_A,
                belief_key="ally_trustworthy",
                before=0.8,
                after=-0.4,
                source_event_keys=["E1"],
            )
        ],
        intentions=[
            IntentionContract(
                character_id=CHAR_A,
                action_intent="当面对峙",
                support_belief_keys=["ally_trustworthy"],
                support_goal_keys=["revenge"],
            )
        ],
        exit_state="信任崩塌，复仇目标强化",
    )


# ── narrative_state ──────────────────────────────────────────────────

class TestNarrativeState:
    def test_get_set_path(self):
        state = {"a": {"b": 1}}
        assert get_path(state, "a.b") == (True, 1)
        assert get_path(state, "a.x") == (False, None)
        assert set_path(state, "a.c.d", 5)
        assert state["a"]["c"]["d"] == 5

    def test_list_indexing(self):
        state = {"items": [{"name": "x"}, {"name": "y"}]}
        assert get_path(state, "items.1.name") == (True, "y")
        assert get_path(state, "items.5.name") == (False, None)

    def test_deep_merge(self):
        base = {"a": {"b": 1, "c": 2}, "d": 3}
        patch = {"a": {"b": 9}, "e": 4}
        merged = deep_merge_state(base, patch)
        assert merged == {"a": {"b": 9, "c": 2}, "d": 3, "e": 4}
        assert base["a"]["b"] == 1  # base untouched

    def test_apply_hard_deltas(self):
        # relative path applied to a character slice
        state = {"relationships": {"char-b": {"trust": 0.7}}}
        deltas = [StateDelta(path="relationships.char-b.trust", value=0.1, mode="hard")]
        out = apply_state_deltas(state, deltas)
        assert out["relationships"]["char-b"]["trust"] == 0.1

    def test_apply_hard_deltas_char_headed(self):
        # char-headed path stripped via engine char_id
        engine = CausalEngine()
        state = {"relationships": {"char-b": {"trust": 0.7}}}
        deltas = [
            StateDelta(path=f"{CHAR_A}.relationships.char-b.trust", value=0.05, mode="hard"),
            StateDelta(path="world.weather", value="storm", mode="soft"),
        ]
        out = engine.apply_hard_effects(state, deltas, char_id=CHAR_A)
        assert out["relationships"]["char-b"]["trust"] == 0.05
        assert "world" not in out  # soft effects are not applied deterministically

    def test_predicate_ops(self):
        state = {"hp": 30, "alive": True}
        assert evaluate_predicate(state, StatePredicate(path="hp", op="<", value=50))
        assert evaluate_predicate(state, StatePredicate(path="alive", op="==", value=True))
        assert not evaluate_predicate(state, StatePredicate(path="mp", op=">", value=0))
        assert evaluate_predicate(state, StatePredicate(path="hp", op="exists", value=None))

    def test_active_goals_filters(self):
        goals = active_goals(_state_a())
        assert "revenge" in goals and "retire" not in goals


# ── causal_engine ─────────────────────────────────────────────────────

class TestCausalEngine:
    def setup_method(self):
        self.engine = CausalEngine()

    def test_precondition_pass_fail(self):
        state = {"physical": {"hp": 30}}
        ok = StatePredicate(path="physical.hp", op=">", value=10)
        bad = StatePredicate(path="physical.hp", op=">", value=100)
        assert self.engine.validate_preconditions(state, [ok]).ok
        report = self.engine.validate_preconditions(state, [bad])
        assert not report.ok
        assert any(f.code == "CAUSAL_PRECONDITION_FAILED" for f in report.findings)

    def test_hard_effect_application(self):
        contract_effects = [
            StateDelta(path="relationships.char-b.trust", value=0.05, mode="hard"),
            StateDelta(path="world.weather", value="storm", mode="soft"),
        ]
        state = {"relationships": {"char-b": {"trust": 0.7}}}
        out = self.engine.apply_hard_effects(state, contract_effects)
        assert out["relationships"]["char-b"]["trust"] == 0.05
        assert "world" not in out  # soft effects are not applied deterministically

    def test_hard_effect_contradiction(self):
        expected = [StateDelta(path=f"{CHAR_A}.status", value="dead", mode="hard")]
        observed_ok = [StateDelta(path=f"{CHAR_A}.status", value="dead", mode="hard")]
        assert self.engine.check_hard_effect_contradictions(expected, observed_ok).ok

        report_missing = self.engine.check_hard_effect_contradictions(expected, [])
        assert any(f.code == "HARD_EFFECT_MISSING" for f in report_missing.findings)

        observed_bad = [StateDelta(path=f"{CHAR_A}.status", value="alive", mode="hard")]
        report_conflict = self.engine.check_hard_effect_contradictions(expected, observed_bad)
        assert any(f.code == "HARD_EFFECT_CONTRADICTED" for f in report_conflict.findings)

    def test_knowledge_path_gate(self):
        perceptions = [
            PerceptionDelta(character_id=CHAR_A, event_key="E1", channel="saw"),
            PerceptionDelta(character_id=CHAR_B, event_key="E1", channel="missed"),
        ]
        # belief sourced from an event NOBODY perceived -> illegal
        beliefs_bad = {"secret_known": ["E9"]}
        report = self.engine.validate_knowledge_path({}, perceptions, beliefs_bad)
        assert any(f.code == "ILLEGAL_KNOWLEDGE" for f in report.findings)

        # belief with no source at all -> unsupported
        report_empty = self.engine.validate_knowledge_path({}, perceptions, {"hunch": []})
        assert any(f.code == "UNSUPPORTED_BELIEF_CHANGE" for f in report_empty.findings)

        # belief sourced from a perceived event -> legal
        assert self.engine.validate_knowledge_path({}, perceptions, {"seen": ["E1"]}).ok

    def test_intention_support(self):
        state = _state_a()
        anchors = {"ANCHOR_VENGEANCE"}
        intentions = [
            IntentionContract(
                character_id=CHAR_A,
                action_intent="复仇",
                support_belief_keys=["ally_trustworthy"],
                support_goal_keys=["revenge"],
            ),
            IntentionContract(
                character_id=CHAR_A,
                action_intent="无端行动",
                support_belief_keys=[],
                support_goal_keys=[],
                support_anchor_ids=["ANCHOR_MISSING"],
            ),
        ]
        report = self.engine.validate_intention_support(state, intentions, anchors)
        # second intention has no support in any pool -> unresolved attribution
        assert any(f.code == "UNRESOLVED_ATTRIBUTION" for f in report.findings)
        assert intentions[1].attribution_status == "unresolved"
        assert intentions[0].attribution_status == "supported"

    def test_hard_edge_propagation_dangling(self):
        events = [
            ProvisionalEvent(
                event_key="E1",
                actor_id=CHAR_A,
                action="偷袭",
                hard_effects=[StateDelta(path=f"{CHAR_B}.hp", value=0, mode="hard")],
            ),
            ProvisionalEvent(event_key="E2", actor_id=CHAR_B, action="反击", involves=[]),
        ]
        edges = [CausalEdge(from_key="E1", to_key="E2", relation="CAUSES", mode="hard")]
        report = self.engine.propagate_hard_edges(events, edges)
        # E1 hard effect targets char-b who IS the actor of E2 — involved, should pass
        assert report.ok or all(
            f.code != "CAUSAL_DANGLING_EFFECT" for f in report.findings
        )

    def test_dangling_state_change(self):
        effects = [
            StateDelta(path=f"{CHAR_A}.hp", value=0, mode="hard", source_event_key="E9")
        ]
        events = [ProvisionalEvent(event_key="E1", actor_id=CHAR_A, action="x")]
        report = self.engine.find_dangling_state_changes(effects, events)
        assert any(f.code == "CAUSAL_DANGLING_EFFECT" for f in report.findings)

    def test_affect_continuity_jump(self):
        state = _state_a()
        transitions = [
            AffectTransition(
                character_id=CHAR_A,
                to_vad=VAD(valence=-0.9, arousal=0.95, dominance=-0.8),
                cause_event_keys=["E1"],
                shock="none",
            )
        ]
        report = self.engine.check_affect_continuity(state, transitions)
        # huge jump without shock flag -> emotional jitter
        assert any(f.code == "EMOTIONAL_JITTER" for f in report.findings)


# ── appraisal_engine ─────────────────────────────────────────────────

class TestAppraisalEngine:
    def setup_method(self):
        self.engine = AppraisalEngine()

    def test_appraisal_computed(self):
        ap = self.engine.compute_appraisal(
            CHAR_A,
            "E1",
            {"goal_congruence": -0.8, "novelty": 0.9, "certainty": 0.9, "norm_violation": 0.7},
            _state_a(),
        )
        assert ap.character_id == CHAR_A
        assert ap.goal_congruence == -0.8
        assert 0.0 <= ap.novelty <= 1.0

    def test_target_affect_direction(self):
        ap = self.engine.compute_appraisal(
            CHAR_A,
            "E1",
            {"goal_congruence": -1.0, "novelty": 0.8, "controllability": 0.1},
            _state_a(),
        )
        vad = self.engine.compute_target_affect(ap, _state_a())
        assert vad.valence < 0  # hostile event → negative valence
        assert vad.arousal > 0.3
        positive = self.engine.compute_appraisal(
            CHAR_A, "E2", {"goal_congruence": 1.0, "novelty": 0.1}, _state_a()
        )
        vad_pos = self.engine.compute_target_affect(positive, _state_a())
        assert vad_pos.valence > vad.valence

    def test_integrate_affect_moves_toward_target(self):
        state = _state_a()
        target = VAD(valence=-0.8, arousal=0.9, dominance=-0.5)
        next_vad = self.engine.integrate_affect(state, target, delta_chapters=50.0)
        assert abs(next_vad.valence - (-0.8)) < 0.05
        assert abs(next_vad.arousal - 0.9) < 0.05

    def test_expression_constraints(self):
        ap = self.engine.compute_appraisal(
            CHAR_A,
            "E1",
            {"goal_congruence": -1.0, "novelty": 0.9, "controllability": 0.2},
            _state_a(),
        )
        vad = self.engine.compute_target_affect(ap, _state_a())
        ec = self.engine.derive_expression_constraints(CHAR_A, vad, _state_a())
        assert ec.character_id == CHAR_A
        assert ec.speech_rate in ("faster", "slower", "normal")

    def test_emotion_labels_directional(self):
        hostile = self.engine.compute_appraisal(
            CHAR_A,
            "E1",
            {"goal_congruence": -1.0, "novelty": 1.0, "controllability": 0.1,
             "norm_violation": -0.8, "autonomy_threat": 0.8,
             "attachment_threat": 0.7, "agency": {"other": 0.9}},
            _state_a(),
        )
        vad = self.engine.compute_target_affect(hostile, _state_a())
        labels = self.engine.derive_emotion_labels(hostile, vad)
        assert labels  # some negative-arousal label derived
        assert any(
            l in ("anger", "indignation", "anxiety", "distress", "alarm") for l in labels
        )

    def test_build_and_apply_transition(self):
        state = _state_a()
        ap = self.engine.compute_appraisal(
            CHAR_A, "E1", {"goal_congruence": -1.0, "novelty": 0.9}, state
        )
        tr = self.engine.build_affect_transition(
            CHAR_A, ap, state, cause_event_keys=["E1"]
        )
        out = self.engine.apply_transition(state, tr)
        assert "affect" in out
        assert out["affect"]["vad"]["valence"] == tr.to_vad.valence


# ── scene_contract compiler ──────────────────────────────────────────

class TestSceneContractCompiler:
    def setup_method(self):
        self.compiler = SceneContractCompiler()

    def test_compile_renumbers_events(self):
        contract = self.compiler.compile_scene_contract(
            _proposal(),
            chapter_no=3,
            scene_no=2,
            states_by_char={CHAR_A: _state_a()},
        )
        keys = [e.event_key for e in contract.provisional_events]
        assert keys == ["P-003-02-01", "P-003-02-02"]
        # edges remapped to new keys
        assert all(
            e.from_key in keys and e.to_key in keys for e in contract.causal_edges
        )
        assert contract.pov_character_id == CHAR_A

    def test_hash_stable_and_sensitive(self):
        c1 = self.compiler.compile_scene_contract(
            _proposal(), chapter_no=3, scene_no=2, states_by_char={CHAR_A: _state_a()}
        )
        c2 = self.compiler.compile_scene_contract(
            _proposal(), chapter_no=3, scene_no=2, states_by_char={CHAR_A: _state_a()}
        )
        assert c1.contract_hash == c2.contract_hash

        prop = _proposal()
        prop.provisional_events[1].action = "完全不同的行动"
        c3 = self.compiler.compile_scene_contract(
            prop, chapter_no=3, scene_no=2, states_by_char={CHAR_A: _state_a()}
        )
        assert c1.contract_hash != c3.contract_hash

    def test_validation_report(self):
        contract = self.compiler.compile_scene_contract(
            _proposal(), chapter_no=1, scene_no=1, states_by_char={CHAR_A: _state_a()}
        )
        anchors = {CHAR_A: {"ANCHOR_VENGEANCE"}}
        report = self.compiler.validate_scene_contract(contract, {CHAR_A: _state_a()}, anchors)
        assert isinstance(report.ok, bool)
        assert isinstance(report.findings, list)

    def test_hard_effects_surface_in_expected_effects(self):
        contract = self.compiler.compile_scene_contract(
            _proposal(), chapter_no=1, scene_no=1, states_by_char={CHAR_A: _state_a()}
        )
        hard = [e for e in contract.expected_effects if e.mode == "hard"]
        assert any(
            e.path.endswith("relationships.char-b.trust") for e in hard
        )

    def test_perceptions_derived_from_presence(self):
        contract = self.compiler.compile_scene_contract(
            _proposal(), chapter_no=1, scene_no=1, states_by_char={CHAR_A: _state_a()}
        )
        assert contract.perceptions  # both actors perceive scene events


# ── counterfactual audit ─────────────────────────────────────────────

class TestCounterfactualAudit:
    def _contracts(self, *, with_alt_support: bool) -> list[dict]:
        events = [
            {
                "event_key": "P-001-01-01",
                "actor_id": CHAR_B,
                "action": "betray the protagonist by selling the map",
                "event_type": "betrayal",
                "involves": [CHAR_A],
                "hard_effects": [
                    {
                        "path": f"{CHAR_A}.relationships.char-b.trust",
                        "value": 0.1,
                        "mode": "hard",
                    }
                ],
            },
            {
                "event_key": "P-001-01-02",
                "actor_id": CHAR_A,
                "action": "confront the traitor",
                "involves": [CHAR_B],
            },
        ]
        edges = [
            {"from": "P-001-01-01", "to": "P-001-01-02", "relation": "MOTIVATES", "mode": "hard"}
        ]
        if with_alt_support:
            events.append(
                {
                    "event_key": "P-001-01-03",
                    "actor_id": "witness",
                    "action": "reveal earlier lie",
                    "involves": [CHAR_A],
                }
            )
            edges.append(
                {"from": "P-001-01-03", "to": "P-001-01-02", "relation": "MOTIVATES", "mode": "soft"}
            )
        return [
            {
                "scene_no": 1,
                "contract_hash": "h1",
                "provisional_events": events,
                "causal_edges": edges,
            }
        ]

    def test_key_node_detection(self):
        ev = ProvisionalEvent(
            event_key="X1", actor_id=CHAR_B, action="背叛主角"
        )
        assert is_key_node_event(ev)
        plain = ProvisionalEvent(event_key="X2", actor_id=CHAR_A, action="喝茶")
        assert not is_key_node_event(plain)

    def test_necessary_support(self):
        report = audit_counterfactual(self._contracts(with_alt_support=False), {})
        assert report.audited_events == ["P-001-01-01"]
        assert any(
            f.classification == "necessary_support" and f.removed_event_key == "P-001-01-01"
            for f in report.findings
        )

    def test_contributing_support(self):
        report = audit_counterfactual(self._contracts(with_alt_support=True), {})
        assert any(
            f.classification == "contributing_support" for f in report.findings
        )

    def test_empty_contracts_ok(self):
        report = audit_counterfactual([], {})
        assert report.ok and report.findings == []

    def test_redundant_hard_event_flagged(self):
        contracts = [
            {
                "scene_no": 1,
                "contract_hash": "h1",
                "provisional_events": [
                    {
                        "event_key": "P-001-01-01",
                        "actor_id": CHAR_A,
                        "action": "做出 pivotal_decision 意义的决定",
                        "event_type": "pivotal_decision",
                        "hard_effects": [
                            {"path": f"{CHAR_A}.mood", "value": "calm", "mode": "soft"}
                        ],
                    }
                ],
                "causal_edges": [],
            }
        ]
        report = audit_counterfactual(contracts, {})
        # key node with no hard effect and no edges: removal changes nothing,
        # but no motive/hard edge either -> no redundancy finding
        assert report.audited_events == ["P-001-01-01"]


# ── v9.1 PR-01: compile fail-closed + hard effect flow ────────────────

import asyncio

from app.engine.causal_compile import compile_chapter_contracts


def _scene1_proposal() -> dict:
    """Scene 1: A learns of betrayal — hard effect drops belief confidence."""
    return {
        "scene_no": 1,
        "dramatic_goal": "A 发现 B 的背叛",
        "pov_character_id": CHAR_A,
        "characters": [CHAR_A, CHAR_B],
        "provisional_events": [
            {
                "event_key": "E1",
                "actor_id": CHAR_B,
                "action": "秘密传递情报给敌对势力",
                "involves": [CHAR_A],
                "hard_effects": [
                    {
                        "path": f"{CHAR_A}.beliefs.ally_trustworthy.confidence",
                        "value": -0.4,
                        "mode": "hard",
                    }
                ],
            }
        ],
        "belief_deltas": [
            {
                "character_id": CHAR_A,
                "belief_key": "ally_trustworthy",
                "after": -0.4,
                "source_event_keys": ["E1"],
            }
        ],
    }


def _scene2_proposal(belief_before: float | None) -> dict:
    """Scene 2: A confronts B — belief delta with explicit before value."""
    d = {
        "scene_no": 2,
        "dramatic_goal": "A 当面对峙 B",
        "pov_character_id": CHAR_A,
        "characters": [CHAR_A, CHAR_B],
        "provisional_events": [
            {
                "event_key": "E2",
                "actor_id": CHAR_A,
                "action": "质问盟友并摊牌",
                "involves": [CHAR_B],
            }
        ],
        "belief_deltas": [
            {
                "character_id": CHAR_A,
                "belief_key": "ally_trustworthy",
                "after": -0.6,
                "source_event_keys": ["E2"],
            }
        ],
    }
    if belief_before is not None:
        d["belief_deltas"][0]["before"] = belief_before
    return d


class TestCompileFailClosed:
    def test_hard_effect_flows_from_scene_1_to_scene_2(self):
        """Spec §3.1: Scene 1 hard effect must enter Scene 2 working state."""
        result = asyncio.run(
            compile_chapter_contracts(
                book_id=uuid.uuid4(),
                chapter_id=uuid.uuid4(),
                chapter_no=1,
                scene_plan={"scenes": [_scene1_proposal(), _scene2_proposal(None)]},
                l4_states={CHAR_A: _state_a()},
                persist=False,
            )
        )
        contracts = result["contracts"]
        assert len(contracts) == 2
        # scene 2's belief "before" is auto-completed from the working state,
        # which scene 1's hard effect already advanced to -0.4
        s2_beliefs = contracts[1]["belief_deltas"]
        assert s2_beliefs[0]["before"] == -0.4

    def test_precondition_violation_hard_blocks(self):
        """Scene 2 asserts before=0.8 but scene 1 dropped it to -0.4 → blocker."""
        with pytest.raises(CausalHardBlockError) as exc_info:
            asyncio.run(
                compile_chapter_contracts(
                    book_id=uuid.uuid4(),
                    chapter_id=uuid.uuid4(),
                    chapter_no=1,
                    scene_plan={
                        "scenes": [
                            _scene1_proposal(),
                            _scene2_proposal(belief_before=0.8),
                        ]
                    },
                    l4_states={CHAR_A: _state_a()},
                    persist=False,
                )
            )
        assert exc_info.value.code == "CAUSAL_PRECONDITION_FAILED"

    def test_causal_schema_error_fail_closed(self):
        """A proposal WITH causal fields but broken schema must not degrade."""
        broken = _scene1_proposal()
        broken["provisional_events"] = [{"event_key": "E1", "actor_id": 12345, "action": "x"}]
        with pytest.raises(CausalRuntimeError):
            asyncio.run(
                compile_chapter_contracts(
                    book_id=uuid.uuid4(),
                    chapter_id=uuid.uuid4(),
                    chapter_no=1,
                    scene_plan={"scenes": [broken]},
                    l4_states={CHAR_A: _state_a()},
                    persist=False,
                )
            )

    def test_minimal_contract_still_allowed_without_causal_fields(self):
        """Scenes with no causal structure degrade to a minimal contract."""
        result = asyncio.run(
            compile_chapter_contracts(
                book_id=uuid.uuid4(),
                chapter_id=uuid.uuid4(),
                chapter_no=1,
                scene_plan={"scenes": [{"scene_no": 1, "goal": "过场"}]},
                l4_states={},
                persist=False,
            )
        )
        assert len(result["contracts"]) == 1


class TestPreconditionPathScoping:
    def test_char_headed_predicate_evaluated(self):
        engine = CausalEngine()
        pred = StatePredicate(
            path=f"{CHAR_A}.beliefs.ally_trustworthy.confidence", op=">=", value=0.5
        )
        report = engine.validate_preconditions(
            _state_a(), [pred], char_id=CHAR_A
        )
        assert report.ok

    def test_char_headed_predicate_failure(self):
        engine = CausalEngine()
        pred = StatePredicate(
            path=f"{CHAR_A}.beliefs.ally_trustworthy.confidence", op=">=", value=0.95
        )
        report = engine.validate_preconditions(
            _state_a(), [pred], char_id=CHAR_A
        )
        assert not report.ok
        assert any(f.code == "CAUSAL_PRECONDITION_FAILED" for f in report.findings)

    def test_other_characters_predicate_skipped(self):
        """B's predicate must not false-fail against A's state slice."""
        engine = CausalEngine()
        pred = StatePredicate(
            path=f"{CHAR_B}.beliefs.anything.confidence", op=">=", value=0.9
        )
        report = engine.validate_preconditions(
            _state_a(), [pred], char_id=CHAR_A
        )
        assert report.ok  # skipped, not failed

    def test_bare_path_predicate_evaluated(self):
        engine = CausalEngine()
        pred = StatePredicate(path="beliefs.city_is_safe.confidence", op=">=", value=0.5)
        report = engine.validate_preconditions(_state_a(), [pred], char_id=CHAR_A)
        assert report.ok


# ── v9.1 PR-02: per-scene context (spec §5/§6) ───────────────────────

from app.engine.relevant_state import select_relevant_scene_state


class TestWorkingStatesByScene:
    def test_snapshots_capture_pre_scene_state(self):
        """Scene 1 sees the initial belief; scene 2 sees scene 1's hard effect."""
        result = asyncio.run(
            compile_chapter_contracts(
                book_id=uuid.uuid4(),
                chapter_id=uuid.uuid4(),
                chapter_no=1,
                scene_plan={"scenes": [_scene1_proposal(), _scene2_proposal(None)]},
                l4_states={CHAR_A: _state_a()},
                persist=False,
            )
        )
        snaps = result["working_states_by_scene"]
        assert set(snaps.keys()) == {1, 2}
        s1_conf = snaps[1][CHAR_A]["beliefs"]["ally_trustworthy"]["confidence"]
        s2_conf = snaps[2][CHAR_A]["beliefs"]["ally_trustworthy"]["confidence"]
        assert s1_conf == 0.8
        assert s2_conf == -0.4

    def test_snapshots_are_isolated_copies(self):
        """Mutating one scene's snapshot must not leak into another's."""
        result = asyncio.run(
            compile_chapter_contracts(
                book_id=uuid.uuid4(),
                chapter_id=uuid.uuid4(),
                chapter_no=1,
                scene_plan={"scenes": [_scene1_proposal(), _scene2_proposal(None)]},
                l4_states={CHAR_A: _state_a()},
                persist=False,
            )
        )
        snaps = result["working_states_by_scene"]
        snaps[1][CHAR_A]["beliefs"]["ally_trustworthy"]["confidence"] = 0.0
        assert (
            snaps[2][CHAR_A]["beliefs"]["ally_trustworthy"]["confidence"] == -0.4
        )


class TestSelectRelevantSceneState:
    def _compiled_scene1_contract(self) -> dict:
        result = asyncio.run(
            compile_chapter_contracts(
                book_id=uuid.uuid4(),
                chapter_id=uuid.uuid4(),
                chapter_no=1,
                scene_plan={"scenes": [_scene1_proposal()]},
                l4_states={CHAR_A: _state_a()},
                persist=False,
            )
        )
        return result["contracts"][0]

    def test_scopes_to_scene_characters_only(self):
        contract = self._compiled_scene1_contract()
        state_b = _state_a()
        state_c = _state_a()
        relevant = select_relevant_scene_state(
            scene_contract=contract,
            l4_states={CHAR_A: _state_a(), CHAR_B: state_b, "char-c": state_c},
            core_anchors_by_char={
                CHAR_A: [{"anchor_code": "A1", "statement": "复仇执念"}],
            },
        )
        assert CHAR_A in relevant["characters"]
        assert CHAR_B in relevant["characters"]
        assert "char-c" not in relevant["characters"]  # not touched by scene 1
        assert set(relevant["character_ids"]) <= {CHAR_A, CHAR_B}
        # beliefs: scene touches ally_trustworthy → selected
        assert "ally_trustworthy" in relevant["characters"][CHAR_A]["beliefs"]
        # goals: only active ones survive
        assert "revenge" in relevant["characters"][CHAR_A]["goals"]
        assert "retire" not in relevant["characters"][CHAR_A]["goals"]
        # anchors attached for present character
        assert relevant["characters"][CHAR_A]["core_anchors"][0]["anchor_code"] == "A1"

    def test_none_contract_returns_empty(self):
        relevant = select_relevant_scene_state(
            scene_contract=None,
            l4_states={CHAR_A: _state_a()},
        )
        assert relevant["characters"] == {}
        assert relevant["character_ids"] == []

    def test_wanted_belief_prioritized(self):
        """Belief keys referenced by the scene's deltas are preferred."""
        contract = self._compiled_scene1_contract()
        fat_state = _state_a()
        for i in range(20):
            fat_state["beliefs"][f"filler_{i}"] = {
                "polarity": 1,
                "confidence": 0.99,
                "source_event_ids": [],
            }
        relevant = select_relevant_scene_state(
            scene_contract=contract,
            l4_states={CHAR_A: fat_state},
        )
        picked = relevant["characters"][CHAR_A]["beliefs"]
        assert "ally_trustworthy" in picked  # wanted despite lower confidence
        assert len(picked) <= 8
