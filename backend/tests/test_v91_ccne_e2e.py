"""v9.1 PR-10: CCNE multi-scene E2E against the §35 stress fixture.

Fixed fixture: 8 characters, 3 scenes, 5 active goals, 12 beliefs,
4 relationships, 10 provisional events, 7 causal edges.

Chain under test (all real engine code, no mocks):
compile_chapter_contracts → run_contract_gate (Pre-Draft)
→ select_relevant_scene_state (per-scene context)
→ run_realization_gate (Post-Draft, events generated from contracts)
→ audit_counterfactual

Acceptance (spec §35):
    required_state_recall        = 1.0
    context_relevance_precision  >= 0.85
    illegal_knowledge            = 0
    hard_effect_missing          = 0
and no scene context contains every character's full state.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.engine.causal_compile import compile_chapter_contracts
from app.engine.causal_errors import CausalHardBlockError
from app.engine.contract_gate import run_contract_gate
from app.engine.counterfactual_audit import audit_counterfactual
from app.engine.realization_gate import run_realization_gate
from app.engine.relevant_state import select_relevant_scene_state

# ── cast (8 characters) ───────────────────────────────────────────────

SHEN = "shen-yan"      # protagonist
LUWAN = "lu-wan"       # ally
LAOSAO = "lao-shaogong"  # mentor
PEI = "pei-zhao"       # rival
SUYUN = "su-yun"       # rival's aide
TANG = "tang-qi"       # background
GUWEI = "gu-wei"       # background
ZHOU = "zhou-ma"       # background

ALL_CHARS = [SHEN, LUWAN, LAOSAO, PEI, SUYUN, TANG, GUWEI, ZHOU]
BACKGROUND_CHARS = {TANG, GUWEI, ZHOU}


def _belief(conf: float, polarity: int = 1) -> dict:
    return {"polarity": polarity, "confidence": conf, "source_event_ids": []}


def _goal(status: str, priority: float) -> dict:
    return {"status": status, "priority": priority, "caused_by_event_ids": []}


def _vad(v: float, a: float, d: float) -> dict:
    return {"valence": v, "arousal": a, "dominance": d}


def _l4_states() -> dict[str, dict]:
    """8 characters / 12 beliefs / 5 active goals / 4 relationships."""
    return {
        SHEN: {
            "beliefs": {
                "jade_is_real": _belief(0.6),
                "ally_trustworthy": _belief(0.8),
                "pei_is_dangerous": _belief(0.5),
                "harbor_has_spy": _belief(0.3),
            },
            "goals": {
                "avenge_father": _goal("active", 0.9),
                "find_truth": _goal("active", 0.8),
                "retire_fishing": _goal("abandoned", 0.1),
            },
            "relationships": {LUWAN: {"trust": 0.7}, PEI: {"trust": -0.6}},
            "affect": {"vad": _vad(-0.2, 0.5, 0.1)},
        },
        LUWAN: {
            "beliefs": {
                "shen_needs_protection": _belief(0.7),
                "secret_must_hide": _belief(0.9),
            },
            "goals": {"protect_shen": _goal("active", 0.7)},
            "relationships": {LAOSAO: {"respect": 0.5}},
            "affect": {"vad": _vad(0.1, 0.4, 0.2)},
        },
        LAOSAO: {
            "beliefs": {
                "shen_can_inherit": _belief(0.75),
                "ferry_must_continue": _belief(0.5),
            },
            "goals": {"guide_shen": _goal("active", 0.6)},
            "affect": {"vad": _vad(0.0, 0.3, 0.4)},
        },
        PEI: {
            "beliefs": {
                "shen_blocks_path": _belief(0.85),
                "jade_belongs_to_me": _belief(0.7),
            },
            "goals": {"seize_jade": _goal("active", 0.85)},
            "relationships": {SHEN: {"trust": -0.7}},
            "affect": {"vad": _vad(-0.3, 0.6, 0.5)},
        },
        SUYUN: {
            "beliefs": {
                "pei_will_win": _belief(0.6),
                "loyalty_pays": _belief(0.4),
            },
            "goals": {"serve_pei": _goal("dormant", 0.5)},
            "affect": {"vad": _vad(0.0, 0.3, 0.3)},
        },
        TANG: {"beliefs": {}, "affect": {"vad": _vad(0.1, 0.2, 0.3)}},
        GUWEI: {"beliefs": {}, "affect": {"vad": _vad(0.0, 0.2, 0.3)}},
        ZHOU: {"beliefs": {}, "affect": {"vad": _vad(0.0, 0.2, 0.2)}},
    }


def _anchors() -> dict[str, list[dict]]:
    return {
        SHEN: [
            {"anchor_code": "ANC-JADE", "statement": "玉佩即父辈遗证，真相比安稳更重要"},
            {"anchor_code": "ANC-MERCY", "statement": "不轻易取人性命"},
        ],
        PEI: [{"anchor_code": "ANC-CLAIM", "statement": "玉佩本该归裴家"}],
    }


# ── scene plan: 3 scenes / 10 events / 7 edges ────────────────────────


def _scene_plan() -> dict:
    return {
        "scenes": [
            # Scene 1 — 灯下揭示 (SHEN / LUWAN / LAOSAO, events E1-E4)
            {
                "scene_no": 1,
                "dramatic_goal": "老艄公揭示玉佩真相，沈砚对陆晚的信任动摇",
                "pov_character_id": SHEN,
                "characters": [SHEN, LUWAN, LAOSAO],
                "provisional_events": [
                    {
                        "event_key": "E1",
                        "actor_id": LAOSAO,
                        "action": "揭示玉佩为赝品的真相",
                        "event_type": "identity_reveal",
                        "involves": [SHEN],
                        "is_public": False,
                        "hard_effects": [
                            {
                                "path": f"{SHEN}.beliefs.jade_is_real.confidence",
                                "value": -0.6,
                                "mode": "hard",
                            }
                        ],
                    },
                    {
                        "event_key": "E2",
                        "actor_id": SHEN,
                        "action": "追问陆晚为何一直隐瞒",
                        "involves": [LUWAN],
                    },
                    {
                        "event_key": "E3",
                        "actor_id": LUWAN,
                        "action": "坦白受老艄公之托隐瞒实情",
                        "involves": [SHEN],
                        "hard_effects": [
                            {
                                "path": f"{SHEN}.beliefs.ally_trustworthy.confidence",
                                "value": 0.3,
                                "mode": "hard",
                            }
                        ],
                    },
                    {
                        "event_key": "E4",
                        "actor_id": SHEN,
                        "action": "决定当夜潜入裴府查证",
                        "event_type": "pivotal_decision",
                    },
                ],
                "causal_edges": [
                    {"from": "E1", "to": "E2", "relation": "REVEALS", "mode": "hard"},
                    {"from": "E2", "to": "E3", "relation": "MOTIVATES", "mode": "soft"},
                    {"from": "E1", "to": "E4", "relation": "CAUSES", "mode": "hard"},
                ],
                "belief_deltas": [
                    {
                        "character_id": SHEN,
                        "belief_key": "jade_is_real",
                        "after": -0.6,
                        "source_event_keys": ["E1"],
                    },
                    {
                        "character_id": SHEN,
                        "belief_key": "ally_trustworthy",
                        "before": 0.8,
                        "after": 0.3,
                        "source_event_keys": ["E3"],
                    },
                ],
                "intentions": [
                    {
                        "character_id": SHEN,
                        "action_intent": "夜探裴府查证玉佩真相",
                        "support_anchor_ids": ["ANC-JADE"],
                        "support_belief_keys": ["jade_is_real"],
                        "support_goal_keys": ["find_truth"],
                        "weight": "pivotal",
                        "attribution_status": "supported",
                    }
                ],
            },
            # Scene 2 — 渡口设伏 (SHEN / PEI / SUYUN, events E5-E7)
            {
                "scene_no": 2,
                "dramatic_goal": "裴照设伏夺玉，沈砚以赝玉脱身",
                "pov_character_id": SHEN,
                "characters": [SHEN, PEI, SUYUN],
                "provisional_events": [
                    {
                        "event_key": "E5",
                        "actor_id": PEI,
                        "action": "于渡口设伏劫夺玉佩",
                        "involves": [SHEN, SUYUN],
                        "is_public": True,
                    },
                    {
                        "event_key": "E6",
                        "actor_id": SHEN,
                        "action": "以赝玉调包金蝉脱壳",
                        "involves": [PEI],
                        "hard_effects": [
                            {
                                "path": f"{PEI}.beliefs.jade_belongs_to_me.confidence",
                                "value": 0.95,
                                "mode": "hard",
                            },
                            {
                                "path": f"{SHEN}.physical.jade_in_hand",
                                "value": "fake-given",
                                "mode": "hard",
                            },
                        ],
                    },
                    {
                        "event_key": "E7",
                        "actor_id": PEI,
                        "action": "察觉受骗后立誓报复",
                        "hard_effects": [
                            {
                                "path": f"{PEI}.beliefs.shen_blocks_path.confidence",
                                "value": 0.95,
                                "mode": "hard",
                            }
                        ],
                    },
                ],
                "causal_edges": [
                    {"from": "E5", "to": "E6", "relation": "CAUSES", "mode": "hard"},
                    {"from": "E6", "to": "E7", "relation": "MOTIVATES", "mode": "soft"},
                ],
                "belief_deltas": [
                    {
                        "character_id": PEI,
                        "belief_key": "jade_belongs_to_me",
                        "before": 0.7,
                        "after": 0.95,
                        "source_event_keys": ["E6"],
                    }
                ],
                "intentions": [
                    {
                        "character_id": PEI,
                        "action_intent": "夺回裴家玉佩",
                        "support_belief_keys": ["jade_belongs_to_me"],
                        "support_goal_keys": ["seize_jade"],
                        "weight": "major",
                        "attribution_status": "supported",
                    }
                ],
            },
            # Scene 3 — 当众对质 (SHEN / LUWAN / PEI, events E8-E10)
            {
                "scene_no": 3,
                "dramatic_goal": "陆晚赶至作证，沈砚揭破伪证并逐走裴照",
                "pov_character_id": SHEN,
                "characters": [SHEN, LUWAN, PEI],
                "provisional_events": [
                    {
                        "event_key": "E8",
                        "actor_id": LUWAN,
                        "action": "赶至渡口当众为沈砚作证",
                        "involves": [SHEN, PEI],
                        "is_public": True,
                        "hard_effects": [
                            {
                                "path": f"{LUWAN}.beliefs.secret_must_hide.confidence",
                                "value": -0.3,
                                "mode": "hard",
                            }
                        ],
                    },
                    {
                        "event_key": "E9",
                        "actor_id": SHEN,
                        "action": "当众揭破裴照的伪证",
                        "involves": [PEI, LUWAN],
                        "is_public": True,
                        "hard_effects": [
                            {
                                "path": f"{PEI}.goals.seize_jade.status",
                                "value": "frustrated",
                                "mode": "hard",
                            }
                        ],
                    },
                    {
                        "event_key": "E10",
                        "actor_id": SHEN,
                        "action": "逐裴照离岛而不取其性命",
                        "involves": [PEI],
                    },
                ],
                "causal_edges": [
                    {"from": "E8", "to": "E9", "relation": "CAUSES", "mode": "hard"},
                    {"from": "E9", "to": "E10", "relation": "MOTIVATES", "mode": "soft"},
                ],
                "belief_deltas": [
                    {
                        "character_id": SHEN,
                        "belief_key": "ally_trustworthy",
                        "after": 0.75,
                        "source_event_keys": ["E8"],
                    }
                ],
            },
        ]
    }


def _compile():
    return asyncio.run(
        compile_chapter_contracts(
            book_id=uuid.uuid4(),
            chapter_id=uuid.uuid4(),
            chapter_no=9,
            scene_plan=_scene_plan(),
            l4_states=_l4_states(),
            core_anchors_by_char=_anchors(),
            persist=False,
        )
    )


# ── metric helpers ────────────────────────────────────────────────────


def _scene_required_chars(contract: dict) -> set[str]:
    """Characters the scene actually touches (same rule as the selector)."""
    ids: set[str] = set()
    if contract.get("pov_character_id"):
        ids.add(str(contract["pov_character_id"]))
    for ev in contract.get("provisional_events") or []:
        if ev.get("actor_id"):
            ids.add(str(ev["actor_id"]))
        ids.update(str(i) for i in ev.get("involves") or [])
    for b in contract.get("belief_deltas") or []:
        ids.add(str(b["character_id"]))
    for it in contract.get("intentions") or []:
        ids.add(str(it["character_id"]))
    return ids


def _scene_required_beliefs(contract: dict) -> set[tuple[str, str]]:
    """(character_id, belief_key) pairs the scene's deltas must see."""
    return {
        (str(b["character_id"]), str(b["belief_key"]))
        for b in contract.get("belief_deltas") or []
    }


def _actual_events_from_contracts(contracts: list[dict]) -> list[dict]:
    """Simulated StateExtractor output: one narrative event per provisional
    event plus one observation per hard effect, all carrying
    realized_provisional_event_key (spec §24 — explicit mapping, no guessing)."""
    events: list[dict] = []
    for c in contracts:
        sn = c["scene_no"]
        for i, ev in enumerate(c.get("provisional_events") or [], start=1):
            events.append(
                {
                    "event_key": f"act-{sn:02d}-{i:02d}",
                    "entity_type": "character",
                    "entity_id": ev.get("actor_id"),
                    "summary": ev.get("action"),
                    "scene_no": sn,
                    "realized_provisional_event_key": ev["event_key"],
                    "evidence_paragraph_key": f"P-009-{sn:02d}-{i:02d}-01",
                }
            )
        for i, eff in enumerate(c.get("expected_effects") or [], start=1):
            if eff.get("mode") != "hard":
                continue
            head, _, rest = eff["path"].partition(".")
            events.append(
                {
                    "event_key": f"obs-{sn:02d}-{i:02d}",
                    "entity_type": "character",
                    "entity_id": head,
                    "field": rest,
                    "old_value": None,
                    "new_value": eff.get("value"),
                    "certainty": "explicit",
                    "scene_no": sn,
                    "evidence_paragraph_key": f"P-009-{sn:02d}-{i:02d}-02",
                    "evidence": "正文文本证据",
                    "realized_provisional_event_key": eff.get("source_event_key"),
                }
            )
    return events


# ── E2E: fixture shape ────────────────────────────────────────────────


class TestFixtureShape:
    def test_fixture_matches_spec_35(self):
        states = _l4_states()
        assert len(states) == 8
        assert sum(len(s.get("beliefs", {})) for s in states.values()) == 12
        active = sum(
            1
            for s in states.values()
            for g in s.get("goals", {}).values()
            if g.get("status") == "active"
        )
        assert active == 5
        assert (
            sum(len(s.get("relationships", {})) for s in states.values()) == 4
        )
        plan = _scene_plan()
        scenes = plan["scenes"]
        assert len(scenes) == 3
        assert sum(len(s["provisional_events"]) for s in scenes) == 10
        assert sum(len(s["causal_edges"]) for s in scenes) == 7


# ── E2E: full CCNE chain ──────────────────────────────────────────────


class TestCCNEMultiSceneE2E:
    def test_compile_contract_gate_and_cross_scene_flow(self):
        result = _compile()
        contracts = result["contracts"]
        assert result["compiled_count"] == 3
        assert result["blockers"] == []

        # Pre-Draft gate: all three scenes pass
        gate = run_contract_gate(contracts, result["reports"])
        assert gate.ok, gate.blockers
        assert gate.contracts_checked == 3

        # cross-scene hard-effect flow: scene 1 E3 set ally_trustworthy to
        # 0.3; scene 3's delta auto-completes its "before" from that value
        s3 = contracts[2]
        delta = next(
            b for b in s3["belief_deltas"] if b["belief_key"] == "ally_trustworthy"
        )
        assert delta["before"] == 0.3

    def test_per_scene_context_recall_and_precision(self):
        result = _compile()
        contracts = result["contracts"]
        snaps = result["working_states_by_scene"]

        total_required = 0
        total_recalled = 0
        total_selected = 0
        total_relevant = 0
        seen_contexts: list[dict] = []

        for c in contracts:
            sn = c["scene_no"]
            relevant = select_relevant_scene_state(
                scene_contract=c,
                l4_states=snaps[sn],
                core_anchors_by_char=_anchors(),
            )
            seen_contexts.append(relevant)
            selected = set(relevant["character_ids"])
            required = _scene_required_chars(c)
            required &= set(ALL_CHARS)

            recalled = len(required & selected)
            total_recalled += recalled
            total_required += len(required)
            total_selected += len(selected)
            total_relevant += len(required & selected)

            # beliefs the scene's deltas reference must be present
            for cid, key in _scene_required_beliefs(c):
                assert key in relevant["characters"][cid]["beliefs"], (
                    f"scene {sn}: belief '{key}' of {cid} missing from context"
                )

            # §35: a scene context must NOT contain every character
            assert len(selected) < len(ALL_CHARS), (
                f"scene {sn} context contains all {len(ALL_CHARS)} characters"
            )
            # background characters never leak into any scene context
            assert not (selected & BACKGROUND_CHARS)

        recall = total_recalled / total_required
        precision = total_relevant / total_selected
        assert recall == 1.0
        assert precision >= 0.85

        # scenes 1-3 each involve a different subset — prove scoping is real
        s1 = set(seen_contexts[0]["character_ids"])
        s2 = set(seen_contexts[1]["character_ids"])
        s3 = set(seen_contexts[2]["character_ids"])
        assert s1 == {SHEN, LUWAN, LAOSAO}
        assert s2 == {SHEN, PEI, SUYUN}
        assert s3 == {SHEN, LUWAN, PEI}
        assert LAOSAO not in s2 and SUYUN not in s1

    def test_realization_gate_full_chapter(self):
        result = _compile()
        contracts = result["contracts"]
        actual = _actual_events_from_contracts(contracts)

        rg = run_realization_gate(scene_contracts=contracts, actual_events=actual)
        assert rg.ok, rg.findings
        s = rg.summary
        # hard_effect_missing = 0
        assert s["hard_effects_realized"] == s["hard_effects_total"] == 7
        # every provisional event precisely mapped (spec §24)
        assert s["hard_edges_mapped"] == s["hard_edges_total"]
        # illegal_knowledge = 0
        illegal = [
            f for f in rg.findings if f["code"] == "ILLEGAL_KNOWLEDGE"
        ]
        assert illegal == []

    def test_realization_gate_blocks_when_hard_effect_dropped(self):
        """Negative control: drop one observation → the gate must fail closed."""
        result = _compile()
        contracts = result["contracts"]
        actual = _actual_events_from_contracts(contracts)
        dropped = [
            e for e in actual if e.get("field") != "beliefs.jade_is_real.confidence"
        ]
        rg = run_realization_gate(scene_contracts=contracts, actual_events=dropped)
        assert not rg.ok
        codes = {f["code"] for f in rg.findings}
        assert "HARD_EFFECT_NOT_REALIZED" in codes
        assert rg.summary["hard_effects_realized"] < rg.summary["hard_effects_total"]

    def test_counterfactual_audit_detects_necessary_support(self):
        """Removing the identity-reveal key node must strip E4's only support."""
        result = _compile()
        contracts = result["contracts"]
        report = audit_counterfactual(contracts, states_by_char=_l4_states())

        e1_key = contracts[0]["provisional_events"][0]["event_key"]
        assert e1_key in report.audited_events  # identity_reveal → key node
        e1_findings = [f for f in report.findings if f.removed_event_key == e1_key]
        assert any(f.classification == "necessary_support" for f in e1_findings)
        # the audit found real state divergence (jade belief never flips)
        assert any(
            f.classification in ("necessary_support", "contributing_support")
            for f in e1_findings
        )

    def test_compile_fails_closed_when_precondition_broken(self):
        """Scene 2 asserts jade_belongs_to_me >= 0.7; corrupt the initial
        state → compile must hard-block, not silently degrade."""
        states = _l4_states()
        states[PEI]["beliefs"]["jade_belongs_to_me"]["confidence"] = 0.2
        with pytest.raises(CausalHardBlockError) as exc_info:
            asyncio.run(
                compile_chapter_contracts(
                    book_id=uuid.uuid4(),
                    chapter_id=uuid.uuid4(),
                    chapter_no=9,
                    scene_plan=_scene_plan(),
                    l4_states=states,
                    core_anchors_by_char=_anchors(),
                    persist=False,
                )
            )
        assert exc_info.value.code == "CAUSAL_PRECONDITION_FAILED"
