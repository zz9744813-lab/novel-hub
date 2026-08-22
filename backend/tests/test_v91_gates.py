"""v9.1 Double Gate unit tests (spec §7, §8).

Pure deterministic tests — no DB, no LLM. Covers:
- contract_gate:      Pre-Draft gate, fail-closed on blocker codes
- realization_gate:   Post-Draft gate, hard effect / knowledge / attribution
- mechanical_gate:    per-scene contract checks, aggregated findings
"""
import asyncio
import uuid

from app.engine.contract_gate import run_contract_gate
from app.engine.realization_gate import run_realization_gate
from app.engine.mechanical_gate import run_mechanical_consistency
from app.engine.causal_compile import compile_chapter_contracts
from tests.test_v9_causal_engine import (
    CHAR_A,
    CHAR_B,
    _scene1_proposal,
    _scene2_proposal,
    _state_a,
)


def _compile_two_scenes() -> list[dict]:
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
    return result["contracts"]


def _minimal_contract(scene_no: int, **overrides) -> dict:
    d = {"scene_no": scene_no, "dramatic_goal": f"场景 {scene_no} 目标"}
    d.update(overrides)
    return d


# ── Pre-Draft Contract Gate ───────────────────────────────────────────


class TestContractGate:
    def test_passes_valid_compiled_contracts(self):
        contracts = _compile_two_scenes()
        result = run_contract_gate(contracts)
        assert result.ok
        assert result.contracts_checked == 2
        assert result.blockers == []

    def test_blocks_belief_source_missing(self):
        contract = _minimal_contract(
            1,
            pov_character_id=CHAR_A,
            provisional_events=[
                {"event_key": "E1", "actor_id": CHAR_B, "action": "行动"}
            ],
            belief_deltas=[
                {
                    "character_id": CHAR_A,
                    "belief_key": "trust",
                    "after": -0.4,
                    "source_event_keys": ["E_MISSING"],
                }
            ],
        )
        result = run_contract_gate([contract])
        assert not result.ok
        codes = {b["code"] for b in result.blockers}
        assert "BELIEF_SOURCE_MISSING" in codes

    def test_blocks_knowledge_boundary_missing(self):
        # A changes belief sourced from E1, but only B perceives E1
        contract = _minimal_contract(
            1,
            pov_character_id=CHAR_A,
            provisional_events=[
                {"event_key": "E1", "actor_id": CHAR_B, "action": "秘密行动", "is_public": False}
            ],
            perceptions=[
                {"character_id": CHAR_B, "event_key": "E1", "channel": "saw"}
            ],
            belief_deltas=[
                {
                    "character_id": CHAR_A,
                    "belief_key": "trust",
                    "after": -0.4,
                    "source_event_keys": ["E1"],
                }
            ],
        )
        result = run_contract_gate([contract])
        assert not result.ok
        codes = {b["code"] for b in result.blockers}
        assert "KNOWLEDGE_BOUNDARY_MISSING" in codes

    def test_blocks_pivotal_intention_unresolved(self):
        contract = _minimal_contract(
            1,
            intentions=[
                {
                    "character_id": CHAR_A,
                    "action_intent": "关键抉择",
                    "weight": "pivotal",
                    "attribution_status": "unresolved",
                }
            ],
        )
        result = run_contract_gate([contract])
        assert not result.ok
        codes = {b["code"] for b in result.blockers}
        assert "PIVOTAL_INTENTION_UNRESOLVED" in codes

    def test_pivotal_intention_supported_passes(self):
        contract = _minimal_contract(
            1,
            intentions=[
                {
                    "character_id": CHAR_A,
                    "action_intent": "关键抉择",
                    "weight": "pivotal",
                    "attribution_status": "supported",
                }
            ],
        )
        result = run_contract_gate([contract])
        assert result.ok

    def test_blocks_invalid_state_path(self):
        contract = _minimal_contract(
            1,
            expected_effects=[
                {"path": "bad path..x", "value": 1, "mode": "hard"}
            ],
        )
        result = run_contract_gate([contract])
        assert not result.ok
        codes = {b["code"] for b in result.blockers}
        assert "INVALID_STATE_PATH" in codes

    def test_blocks_hard_effect_conflict(self):
        contract = _minimal_contract(
            1,
            expected_effects=[
                {"path": f"{CHAR_A}.relationships.{CHAR_B}.trust", "value": 0.1, "mode": "hard"},
                {"path": f"{CHAR_A}.relationships.{CHAR_B}.trust", "value": -0.5, "mode": "hard"},
            ],
        )
        result = run_contract_gate([contract])
        assert not result.ok
        codes = {b["code"] for b in result.blockers}
        assert "HARD_EFFECT_CONFLICT" in codes

    def test_blocks_from_compile_reports(self):
        reports = [
            {
                "scene_no": 1,
                "ok": False,
                "findings": [
                    {
                        "code": "CAUSAL_PRECONDITION_FAILED",
                        "severity": "blocker",
                        "detail": "前置条件失败",
                    }
                ],
            }
        ]
        result = run_contract_gate([_minimal_contract(1)], reports=reports)
        assert not result.ok
        assert result.blockers[0]["code"] == "CAUSAL_PRECONDITION_FAILED"

    def test_unparseable_contract_blocks(self):
        result = run_contract_gate([{"scene_no": 1, "bogus_field": {}}])
        assert not result.ok
        assert result.blockers[0]["code"] == "CONTRACT_UNPARSEABLE"


# ── Post-Draft Realization Gate ───────────────────────────────────────


def _contract_with_hard_effect() -> dict:
    return _minimal_contract(
        1,
        pov_character_id=CHAR_A,
        provisional_events=[
            {"event_key": "P-001-01-01", "actor_id": CHAR_B, "action": "背叛"}
        ],
        causal_edges=[
            {"from_key": "P-001-01-01", "to_key": "P-001-01-02", "relation": "MOTIVATES", "mode": "soft"}
        ],
        expected_effects=[
            {
                "path": f"{CHAR_A}.beliefs.ally_trustworthy.confidence",
                "value": -0.4,
                "mode": "hard",
                "source_event_key": "P-001-01-01",
            }
        ],
    )


def _belief_event(value=-0.4, **overrides) -> dict:
    ev = {
        "event_key": "evt-01",
        "entity_type": "character",
        "entity_id": CHAR_A,
        "field": "beliefs.ally_trustworthy.confidence",
        "old_value": 0.8,
        "new_value": value,
        "certainty": "explicit",
        "scene_no": 1,
        "evidence_paragraph_key": "P-001-01-01-02",
        "evidence": "文本证据",
    }
    ev.update(overrides)
    return ev


class TestRealizationGate:
    def test_hard_effect_realized_passes(self):
        rg = run_realization_gate(
            scene_contracts=[_contract_with_hard_effect()],
            actual_events=[_belief_event()],
        )
        assert rg.ok, rg.findings
        assert rg.summary["hard_effects_realized"] == 1

    def test_hard_effect_not_realized_blocks(self):
        rg = run_realization_gate(
            scene_contracts=[_contract_with_hard_effect()],
            actual_events=[
                _belief_event(field="beliefs.other.confidence")
            ],
        )
        assert not rg.ok
        codes = {f["code"] for f in rg.findings}
        assert "HARD_EFFECT_NOT_REALIZED" in codes

    def test_hard_effect_contradicted_blocks(self):
        rg = run_realization_gate(
            scene_contracts=[_contract_with_hard_effect()],
            actual_events=[_belief_event(value=0.9)],
        )
        assert not rg.ok
        codes = {f["code"] for f in rg.findings}
        assert "HARD_EFFECT_CONTRADICTED" in codes

    def test_realization_via_explicit_deltas(self):
        rg = run_realization_gate(
            scene_contracts=[_contract_with_hard_effect()],
            actual_events=[],
            actual_state_deltas=[
                {"path": f"{CHAR_A}.beliefs.ally_trustworthy.confidence", "value": -0.4, "mode": "hard"}
            ],
        )
        assert rg.ok, rg.findings

    def test_pivotal_attribution_unresolved_blocks(self):
        rg = run_realization_gate(
            scene_contracts=[_minimal_contract(1)],
            actual_events=[_belief_event()],
            reaction_evidence=[
                {"reaction_key": "R1", "character_id": CHAR_A, "scene_no": 1,
                 "reaction_summary": "关键抉择", "weight": "pivotal"}
            ],
            attributions=[
                {"reaction_key": "R1", "status": "unresolved"}
            ],
        )
        assert not rg.ok
        codes = {f["code"] for f in rg.findings}
        assert "PIVOTAL_ATTRIBUTION_UNRESOLVED" in codes

    def test_pivotal_attribution_supported_passes(self):
        rg = run_realization_gate(
            scene_contracts=[_minimal_contract(1)],
            actual_events=[_belief_event()],
            reaction_evidence=[
                {"reaction_key": "R1", "character_id": CHAR_A, "scene_no": 1,
                 "reaction_summary": "关键抉择", "weight": "pivotal"}
            ],
            attributions=[
                {"reaction_key": "R1", "status": "supported",
                 "core_anchor_ids": ["ANC-1"]}
            ],
        )
        assert rg.ok, rg.findings

    def test_illegal_knowledge_blocks(self):
        rg = run_realization_gate(
            scene_contracts=[_minimal_contract(1)],
            actual_events=[_belief_event()],
            attributions=[
                {"reaction_key": "R1", "status": "supported",
                 "cause_event_keys": ["evt-gHOST"]}
            ],
        )
        assert not rg.ok
        codes = {f["code"] for f in rg.findings}
        assert "ILLEGAL_KNOWLEDGE" in codes

    def test_belief_change_without_evidence_major(self):
        rg = run_realization_gate(
            scene_contracts=[_minimal_contract(1)],
            actual_events=[_belief_event(evidence_paragraph_key=None)],
        )
        assert not rg.ok
        codes = {f["code"] for f in rg.findings}
        assert "BELIEF_CHANGE_WITHOUT_EVIDENCE" in codes

    def test_hard_edge_mapping_missing_when_no_realized_keys(self):
        contract = _contract_with_hard_effect()
        contract["causal_edges"] = [
            {"from_key": "P-001-01-01", "to_key": "P-001-01-02",
             "relation": "MOTIVATES", "mode": "hard"}
        ]
        rg = run_realization_gate(
            scene_contracts=[contract],
            actual_events=[_belief_event()],
        )
        assert not rg.ok
        codes = {f["code"] for f in rg.findings}
        assert "HARD_EDGE_MAPPING_MISSING" in codes

    def test_hard_edge_mapped_passes(self):
        contract = _contract_with_hard_effect()
        contract["causal_edges"] = [
            {"from_key": "P-001-01-01", "to_key": "P-001-01-02",
             "relation": "MOTIVATES", "mode": "hard"}
        ]
        rg = run_realization_gate(
            scene_contracts=[contract],
            actual_events=[
                _belief_event(realized_provisional_event_key="P-001-01-01"),
                _belief_event(
                    event_key="evt-02",
                    field="goals.revenge.priority",
                    realized_provisional_event_key="P-001-01-02",
                ),
            ],
        )
        assert rg.ok, rg.findings
        assert rg.summary["hard_edges_mapped"] == 1

    def test_unresolved_attribution_as_canon_major(self):
        rg = run_realization_gate(
            scene_contracts=[_minimal_contract(1)],
            actual_events=[_belief_event()],
            attributions=[
                {"reaction_key": "R1", "status": "unresolved",
                 "cause_event_keys": ["evt-01"]}
            ],
        )
        assert not rg.ok
        codes = {f["code"] for f in rg.findings}
        assert "UNRESOLVED_ATTRIBUTION_AS_CANON" in codes

    def test_supported_attribution_without_support_major(self):
        rg = run_realization_gate(
            scene_contracts=[_minimal_contract(1)],
            actual_events=[_belief_event()],
            attributions=[
                {"reaction_key": "R1", "status": "supported"}
            ],
        )
        assert not rg.ok
        codes = {f["code"] for f in rg.findings}
        assert "ATTRIBUTION_STATUS_INVALID" in codes


# ── MechanicalGate per scene (spec §8) ────────────────────────────────


class TestMechanicalGatePerScene:
    def test_findings_aggregated_across_scenes(self):
        res = run_mechanical_consistency(
            chapter_content="正文。" * 120,
            scenes=[
                {"scene_no": 1, "content": "干净的第一场景。"},
                {"scene_no": 2, "content": "第二场景出现了禁忌词组。"},
            ],
            outline_data={},
            scene_contracts={
                1: _minimal_contract(1),
                2: _minimal_contract(2, must_not_assert=["禁忌词组"]),
            },
        )
        assert not res.ok
        hits = [
            f for f in res.findings
            if f["code"] == "contract_must_not_assert_present"
        ]
        assert len(hits) == 1
        assert hits[0]["scene_no"] == 2

    def test_per_scene_pre_state_used(self):
        # scene 2's contract requires a precondition that only holds in
        # scene 2's own working state — per-scene states must be respected
        contract2 = _minimal_contract(
            2,
            pov_character_id=CHAR_A,
            preconditions=[
                {"path": f"{CHAR_A}.beliefs.ally_trustworthy.confidence",
                 "op": "<", "value": 0.0}
            ],
        )
        res = run_mechanical_consistency(
            chapter_content="正文。" * 120,
            scenes=[
                {"scene_no": 1, "content": "第一场景。"},
                {"scene_no": 2, "content": "第二场景。"},
            ],
            outline_data={},
            scene_contracts={1: _minimal_contract(1), 2: contract2},
            pre_states_by_scene={
                1: {CHAR_A: _state_a()},  # confidence 0.8 → precondition fails
                2: {CHAR_A: {"beliefs": {"ally_trustworthy": {"confidence": -0.4}}}},  # passes
            },
        )
        scene2_precond = [
            f for f in res.findings
            if f.get("scene_no") == 2 and f["code"] == "CAUSAL_PRECONDITION_FAILED"
        ]
        assert scene2_precond == []  # scene 2 passes with its own state

        # but scene 1's contract against scene 1's state (0.8) must fail
        contract1 = _minimal_contract(
            1,
            pov_character_id=CHAR_A,
            preconditions=[
                {"path": f"{CHAR_A}.beliefs.ally_trustworthy.confidence",
                 "op": "<", "value": 0.0}
            ],
        )
        res2 = run_mechanical_consistency(
            chapter_content="正文。" * 120,
            scenes=[
                {"scene_no": 1, "content": "第一场景。"},
                {"scene_no": 2, "content": "第二场景。"},
            ],
            outline_data={},
            scene_contracts={1: contract1, 2: contract2},
            pre_states_by_scene={
                1: {CHAR_A: _state_a()},
                2: {CHAR_A: {"beliefs": {"ally_trustworthy": {"confidence": -0.4}}}},
            },
        )
        scene1_precond = [
            f for f in res2.findings
            if f.get("scene_no") == 1 and f["code"] == "CAUSAL_PRECONDITION_FAILED"
        ]
        assert scene1_precond, "scene 1 should fail against its own pre-state"

    def test_no_contract_scenes_skipped(self):
        res = run_mechanical_consistency(
            chapter_content="正文。" * 120,
            scenes=[{"scene_no": 1, "content": "第一场景。"}],
            outline_data={},
            scene_contracts={},
        )
        assert res.ok
