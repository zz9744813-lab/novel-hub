"""v9.0 Cognitive-Causal engine (spec §20.2).

Deterministic validation and simulation over narrative state.
No LLM. No prose generation. Python executes constraints; the LLM proposes.

Error codes (spec §27.1) surface to MechanicalGate v9 and Review.
"""
from __future__ import annotations

from typing import Any

from app.contracts.narrative import (
    CausalEdge,
    ContractValidationReport,
    IntentionContract,
    PerceptionDelta,
    ProvisionalEvent,
    SceneContract,
    StateDelta,
    StatePredicate,
    VAD,
)
from app.engine.cognitive_config import DEFAULT_CAUSAL_CONFIG
from app.engine.narrative_state import (
    COGNITIVE_SECTIONS,
    affect_profile,
    affect_vad,
    active_beliefs,
    active_goals,
    evaluate_predicate,
    get_path,
    normalize_state,
)

HARD_EDGE_RELATIONS = {"CAUSES", "PREVENTS"}

# Path heads that address the state directly (not a character-scoped prefix).
# Legacy "characters.{cid}.…" prefixed predicates are skipped (head unknown).
_BARE_PATH_HEADS = frozenset(COGNITIVE_SECTIONS) | {"flat", "world"}


class CausalEngine:
    """Validates that a scene's causal structure holds against state + edges."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.cfg = {**DEFAULT_CAUSAL_CONFIG, **(config or {})}

    # ── precondition validation (spec §20.2) ──────────────────────

    def validate_preconditions(
        self,
        state: dict[str, Any],
        preconditions: list[StatePredicate],
        *,
        scene_no: int | None = None,
        char_id: str | None = None,
    ) -> ContractValidationReport:
        """Validate preconditions against a state (optionally one character's slice).

        Path conventions when char_id is given:
        - ``{char_id}.x.y``  → head stripped, evaluated against the slice
        - bare ``x.y``       → evaluated as-is (global/relative path)
        - other headed paths → another character's predicate: not evaluable
          against this slice, skipped rather than false-failed
        """
        report = ContractValidationReport()
        normalized = normalize_state(state)
        for pred in preconditions:
            check = pred
            if char_id:
                head = pred.path.split(".")[0]
                if head == char_id and "." in pred.path:
                    payload = pred.model_dump()
                    payload["path"] = pred.path.split(".", 1)[1]
                    check = StatePredicate.model_validate(payload)
                elif head != char_id and head not in _BARE_PATH_HEADS:
                    continue
            if not evaluate_predicate(normalized, check):
                report.add(
                    "CAUSAL_PRECONDITION_FAILED",
                    "blocker",
                    detail=f"前置条件不成立: {pred.describe()}",
                    scene_no=scene_no,
                    path=pred.path,
                )
        return report

    # ── hard effects (spec §17) ───────────────────────────────────

    def apply_hard_effects(
        self,
        state: dict[str, Any],
        deltas: list[StateDelta],
        *,
        char_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply hard effect deltas onto a cloned state.

        Char-headed paths ("{char_id}.x.y") are stripped to the relative
        path when char_id matches, so single-character slices stay flat.
        """
        from app.engine.narrative_state import apply_state_deltas

        hard_only = [d for d in deltas if d.mode == "hard"]
        if char_id:
            stripped = []
            for d in hard_only:
                head = d.path.split(".")[0]
                if head == char_id and "." in d.path:
                    payload = d.model_dump()
                    payload["path"] = d.path.split(".", 1)[1]
                    stripped.append(StateDelta.model_validate(payload))
                else:
                    stripped.append(d)
            hard_only = stripped
        return apply_state_deltas(normalize_state(state), hard_only)

    def check_hard_effect_contradictions(
        self,
        expected_effects: list[StateDelta],
        observed_effects: list[StateDelta],
        *,
        scene_no: int | None = None,
    ) -> ContractValidationReport:
        """Expected hard effects must be realized, not contradicted (CC-05)."""
        report = ContractValidationReport()
        observed_by_path = {d.path: d for d in observed_effects if d.mode == "hard"}
        for eff in expected_effects:
            if eff.mode != "hard":
                continue
            obs = observed_by_path.get(eff.path)
            if obs is None:
                report.add(
                    "HARD_EFFECT_MISSING",
                    "blocker",
                    detail=f"硬效应未落实: {eff.path} = {eff.value!r}",
                    scene_no=scene_no,
                    path=eff.path,
                )
            elif _values_conflict(eff.value, obs.value):
                report.add(
                    "HARD_EFFECT_CONTRADICTED",
                    "blocker",
                    detail=f"硬效应被违背: {eff.path} 期望 {eff.value!r}，实际 {obs.value!r}",
                    scene_no=scene_no,
                    path=eff.path,
                )
        return report

    # ── knowledge legality (spec §8, CC-03) ───────────────────────

    def validate_knowledge_path(
        self,
        state: dict[str, Any],
        perceptions: list[PerceptionDelta],
        belief_sources: dict[str, list[str]],
        *,
        scene_no: int | None = None,
    ) -> ContractValidationReport:
        """Any belief/knowledge delta must trace to a perception channel."""
        report = ContractValidationReport()
        perceived_events = {p.event_key for p in perceptions if p.channel != "missed"}
        for belief_key, source_keys in belief_sources.items():
            if not source_keys:
                report.add(
                    "UNSUPPORTED_BELIEF_CHANGE",
                    "blocker",
                    detail=f"信念 {belief_key} 的变化没有任何来源事件",
                    scene_no=scene_no,
                )
                continue
            unperceived = [k for k in source_keys if k not in perceived_events]
            if unperceived:
                report.add(
                    "ILLEGAL_KNOWLEDGE",
                    "blocker",
                    detail=(
                        f"信念 {belief_key} 依赖未被感知的事件 {unperceived}，"
                        "角色不可能知道该信息"
                    ),
                    scene_no=scene_no,
                )
        return report

    # ── intention support (spec §6, CC-04) ────────────────────────

    def validate_intention_support(
        self,
        state: dict[str, Any],
        intentions: list[IntentionContract],
        core_anchor_ids: set[str] | None = None,
        *,
        scene_no: int | None = None,
    ) -> ContractValidationReport:
        """Major/pivotal actions need support from Core/Belief/Goal/World."""
        report = ContractValidationReport()
        normalized = normalize_state(state)
        anchors = core_anchor_ids or set()
        beliefs = set(active_beliefs(normalized).keys())
        goals = set(active_goals(normalized).keys())

        for it in intentions:
            has_support = bool(
                set(it.support_anchor_ids) & anchors
                or set(it.support_belief_keys) & beliefs
                or set(it.support_goal_keys) & goals
                or it.support_event_keys
            )
            if has_support and it.attribution_status == "unresolved":
                it.attribution_status = "supported"
            if not has_support:
                it.attribution_status = "unresolved"
                if it.weight == "pivotal":
                    severity = "blocker" if self.cfg.get("unresolved_pivotal_blocks", True) else "major"
                elif it.weight == "major":
                    severity = "major" if self.cfg.get("unresolved_major_is_review_issue", True) else "minor"
                else:
                    severity = "minor"
                report.add(
                    "UNRESOLVED_ATTRIBUTION",
                    severity,
                    detail=(
                        f"角色 {it.character_id} 的重要行动「{it.action_intent}」"
                        "没有 Core/Belief/Goal/World Constraint 支撑"
                    ),
                    scene_no=scene_no,
                    character_id=it.character_id,
                )
                if severity in ("blocker", "major"):
                    report.add(
                        "UNJUSTIFIED_MOTIVATION",
                        severity,
                        detail=f"动机不足以支撑行动: {it.action_intent}",
                        scene_no=scene_no,
                        character_id=it.character_id,
                    )
        return report

    # ── hard edge propagation (spec §20.2) ────────────────────────

    def propagate_hard_edges(
        self,
        events: list[ProvisionalEvent],
        edges: list[CausalEdge],
        *,
        scene_no: int | None = None,
    ) -> ContractValidationReport:
        """Hard CAUSES/PREVENTS edges imply their hard effects must fire."""
        report = ContractValidationReport()
        by_key = {e.event_key: e for e in events}
        for edge in edges:
            if edge.mode != "hard" or edge.relation not in HARD_EDGE_RELATIONS:
                continue
            src = by_key.get(edge.from_key)
            dst = by_key.get(edge.to_key)
            if src is None or dst is None:
                report.add(
                    "CAUSAL_INVERSION",
                    "major",
                    detail=f"硬边 {edge.from_key}→{edge.to_key} 引用了不存在的事件",
                    scene_no=scene_no,
                )
                continue
            # hard CAUSES: effects listed on source must match target involvement
            if edge.relation == "CAUSES" and src.hard_effects:
                target_involves = set(dst.involves) | {dst.actor_id}
                for eff in src.hard_effects:
                    target_char = eff.path.split(".")[0] if "." in eff.path else None
                    if target_char and target_char not in target_involves:
                        report.add(
                            "CAUSAL_DANGLING_EFFECT",
                            "major",
                            detail=(
                                f"事件 {src.event_key} 的硬效应 {eff.path} "
                                f"与目标事件 {dst.event_key} 的参与者无关联"
                            ),
                            scene_no=scene_no,
                            path=eff.path,
                        )
        return report

    # ── dangling state changes (spec §20.2) ───────────────────────

    def find_dangling_state_changes(
        self,
        expected_effects: list[StateDelta],
        events: list[ProvisionalEvent],
        *,
        scene_no: int | None = None,
    ) -> ContractValidationReport:
        """Every state change needs a source event (no unexplained changes)."""
        report = ContractValidationReport()
        event_keys = {e.event_key for e in events}
        for eff in expected_effects:
            src = eff.source_event_key
            if src is not None and src not in event_keys:
                report.add(
                    "CAUSAL_DANGLING_EFFECT",
                    "major",
                    detail=f"状态变化 {eff.path} 引用了不存在的来源事件 {src}",
                    scene_no=scene_no,
                    path=eff.path,
                )
            elif src is None and eff.mode == "hard":
                report.add(
                    "CAUSAL_DANGLING_EFFECT",
                    "minor",
                    detail=f"硬状态变化 {eff.path} 没有显式来源事件",
                    scene_no=scene_no,
                    path=eff.path,
                )
        return report

    # ── emotion continuity (spec §12) ─────────────────────────────

    def check_affect_continuity(
        self,
        state: dict[str, Any],
        affect_transitions: list[Any],
        *,
        scene_weight: str = "minor",
        scene_no: int | None = None,
    ) -> ContractValidationReport:
        """VAD jumps without explicit shock are EMOTIONAL_JITTER (CC-09)."""
        report = ContractValidationReport()
        normalized = normalize_state(state)
        cur = _vad_from_tuple(affect_vad(normalized))
        threshold = (
            self.cfg.get("affect_jump_major", 0.55)
            if scene_weight == "major"
            else self.cfg.get("affect_jump_minor", 0.35)
        )
        for tr in affect_transitions:
            to_vad = tr.to_vad if isinstance(tr.to_vad, VAD) else _vad_from_tuple(tr.to_vad)
            dist = cur.distance(to_vad)
            if dist > threshold and tr.shock in (None, "none"):
                report.add(
                    "EMOTIONAL_JITTER",
                    "major" if dist > threshold * 1.5 else "minor",
                    detail=(
                        f"角色 {tr.character_id} 情绪从 {cur.to_list()} 跳变到 "
                        f"{to_vad.to_list()}（距离 {dist:.2f} > {threshold}）但没有显式强刺激"
                    ),
                    scene_no=scene_no,
                    character_id=tr.character_id,
                )
            if not tr.cause_event_keys and tr.from_vad is not None:
                report.add(
                    "EMOTION_WITHOUT_CAUSE",
                    "minor",
                    detail=f"角色 {tr.character_id} 的情绪变化没有 cause_event",
                    scene_no=scene_no,
                    character_id=tr.character_id,
                )
        return report

    # ── top-level scene simulation ────────────────────────────────

    def simulate_scene(
        self,
        contract: SceneContract,
        state: dict[str, Any],
        core_anchor_ids: set[str] | None = None,
    ) -> tuple[dict[str, Any], ContractValidationReport]:
        """Full deterministic pass: validate + apply hard effects, return next state."""
        pov_or_first = (
            contract.pov_character_id
            or (contract.relevant_entity_ids[0] if contract.relevant_entity_ids else None)
        )
        report = ContractValidationReport()
        report.merge(
            self.validate_preconditions(
                state,
                contract.preconditions,
                scene_no=contract.scene_no,
                char_id=pov_or_first,
            )
        )
        report.merge(
            self.validate_knowledge_path(
                state,
                contract.perceptions,
                {
                    b.belief_key: b.source_event_keys
                    for b in contract.belief_deltas
                },
                scene_no=contract.scene_no,
            )
        )
        report.merge(
            self.validate_intention_support(
                state, contract.intentions, core_anchor_ids, scene_no=contract.scene_no
            )
        )
        report.merge(
            self.propagate_hard_edges(
                contract.provisional_events, contract.causal_edges, scene_no=contract.scene_no
            )
        )
        report.merge(
            self.find_dangling_state_changes(
                contract.expected_effects, contract.provisional_events, scene_no=contract.scene_no
            )
        )
        report.merge(
            self.check_affect_continuity(
                state, contract.affect_transitions, scene_no=contract.scene_no
            )
        )
        next_state = self.apply_hard_effects(state, contract.expected_effects, char_id=pov_or_first)
        return next_state, report


def _values_conflict(expected: Any, observed: Any) -> bool:
    if expected is None or observed is None:
        return False
    try:
        return expected != observed
    except Exception:
        return False


def _vad_from_tuple(t: Any) -> VAD:
    if isinstance(t, VAD):
        return t
    if isinstance(t, dict):
        return VAD.model_validate(t)
    if isinstance(t, (list, tuple)) and len(t) >= 3:
        return VAD(valence=t[0], arousal=t[1], dominance=t[2])
    return VAD()
