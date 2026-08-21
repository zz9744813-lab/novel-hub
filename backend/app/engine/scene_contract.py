"""v9.0 Scene Contract compiler (spec §18, §20.4, §21, §62).

Compiles a planner SceneProposal + canonical state into a strict
SceneContract: renumbers provisional events, auto-derives perceptions,
completes appraisals/affect via the AppraisalEngine, validates the causal
structure, and hashes the result.

LLM proposes; this module compiles and verifies. No LLM calls here.
"""
from __future__ import annotations

from typing import Any

from app.contracts.narrative import (
    BeliefDelta,
    CausalEdge,
    CharacterAppraisal,
    ContractValidationReport,
    PerceptionDelta,
    ProvisionalEvent,
    SceneContract,
    SceneProposal,
    StateDelta,
    StatePredicate,
)
from app.engine.appraisal_engine import AppraisalEngine
from app.engine.causal_engine import CausalEngine
from app.engine.cognitive_config import DEFAULT_CAUSAL_CONFIG
from app.engine.narrative_state import (
    active_beliefs,
    active_goals,
    get_path,
    normalize_state,
)


class SceneContractCompiler:
    def __init__(self, config: dict[str, Any] | None = None):
        self.cfg = {**DEFAULT_CAUSAL_CONFIG, **(config or {})}
        self.causal = CausalEngine(self.cfg)
        self.appraisal = AppraisalEngine(self.cfg)

    # ── relevant state selection (spec §21) ───────────────────────

    def select_relevant_state(
        self,
        scene: SceneProposal,
        states_by_char: dict[str, dict[str, Any]],
        core_anchors_by_char: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        """Choose only state that actually participates in this scene's causal chain."""
        anchors = core_anchors_by_char or {}
        present = self.scene_characters(scene, states_by_char)
        max_beliefs = int(self.cfg.get("max_relevant_beliefs_per_scene", 8))
        max_anchors = int(self.cfg.get("max_core_anchors_per_scene", 5))

        relevant: dict[str, Any] = {}
        for cid in present:
            normalized = normalize_state(states_by_char.get(cid) or {})
            entry: dict[str, Any] = {}

            beliefs = active_beliefs(normalized)
            goals = active_goals(normalized)
            goal_keys = set(goals.keys())
            # beliefs tied to active goals or referenced by scene proposals first
            referenced = set(scene.emotion_change or "") and set()
            scored_beliefs = sorted(
                beliefs.items(),
                key=lambda kv: -float(kv[1].get("confidence", 0.0) if isinstance(kv[1], dict) else 0),
            )[:max_beliefs]
            entry["beliefs"] = {
                k: v
                for k, v in scored_beliefs
                if k in goal_keys or _floaty(v.get("confidence", 0)) >= 0.5 or k in referenced
            } or dict(scored_beliefs[: max_beliefs // 2])
            entry["goals"] = goals
            entry["affect"] = normalized.get("affect") or {}
            entry["physical"] = normalized.get("physical") or {}
            entry["relationships"] = {
                k: v
                for k, v in (normalized.get("relationships") or {}).items()
                if k in present
            }
            char_anchors = anchors.get(cid) or []
            entry["core_anchors"] = char_anchors[:max_anchors]
            relevant[cid] = entry
        return relevant

    def scene_characters(
        self, scene: SceneProposal, states_by_char: dict[str, dict[str, Any]]
    ) -> list[str]:
        ids: list[str] = []
        if scene.pov_character_id:
            ids.append(str(scene.pov_character_id))
        for c in scene.characters or []:
            cid = _as_id(c)
            if cid:
                ids.append(cid)
        for e in scene.provisional_events:
            if e.actor_id:
                ids.append(str(e.actor_id))
            ids.extend(str(i) for i in e.involves if i)
        for b in scene.belief_deltas:
            ids.append(str(b.character_id))
        for it in scene.intentions:
            ids.append(str(it.character_id))
        seen: set[str] = set()
        ordered = [i for i in ids if not (i in seen or seen.add(i))]
        # keep only characters we actually have state for, plus preserve order
        return [i for i in ordered if i in states_by_char] or ordered[:10]

    # ── provisional event keys (spec §62) ─────────────────────────

    def _renumber_events(
        self, scene: SceneProposal, chapter_no: int, scene_no: int
    ) -> list[ProvisionalEvent]:
        out: list[ProvisionalEvent] = []
        for seq, ev in enumerate(scene.provisional_events, start=1):
            new_key = f"P-{chapter_no:03d}-{scene_no:02d}-{seq:02d}"
            data = ev.model_dump(by_alias=True)
            old_key = data.get("event_key")
            data["event_key"] = new_key
            data["_original_key"] = old_key
            out.append(ProvisionalEvent.model_validate(data))
        return out

    def _remap_edges(self, edges: list[CausalEdge], key_map: dict[str, str]) -> list[CausalEdge]:
        remapped: list[CausalEdge] = []
        for e in edges:
            data = e.model_dump(by_alias=True)
            data["from"] = key_map.get(data["from"], data["from"])
            data["to"] = key_map.get(data["to"], data["to"])
            remapped.append(CausalEdge.model_validate(data))
        return remapped

    # ── perceptions (spec §8) ─────────────────────────────────────

    def _derive_perceptions(
        self,
        events: list[ProvisionalEvent],
        scene: SceneProposal,
    ) -> list[PerceptionDelta]:
        """Everyone present perceives public events; private events only reach
        actor + explicit involves."""
        perceptions: list[PerceptionDelta] = []
        present = set(self._present_ids(scene, events))
        for ev in events:
            if ev.is_public:
                audience = present - ({str(ev.actor_id)} if ev.actor_id else set())
            else:
                audience = {str(i) for i in ev.involves if i} - (
                    {str(ev.actor_id)} if ev.actor_id else set()
                )
            for cid in sorted(audience):
                perceptions.append(
                    PerceptionDelta(
                        character_id=cid,
                        event_key=ev.event_key,
                        channel="saw",
                        detail=f"在场感知: {ev.action[:60]}",
                    )
                )
        return perceptions

    def _present_ids(self, scene: SceneProposal, events: list[ProvisionalEvent]) -> set[str]:
        ids: set[str] = set()
        if scene.pov_character_id:
            ids.add(str(scene.pov_character_id))
        for c in scene.characters or []:
            cid = _as_id(c)
            if cid:
                ids.add(cid)
        for e in events:
            if e.actor_id:
                ids.add(str(e.actor_id))
            ids.update(str(i) for i in e.involves if i)
        return ids

    # ── compile (spec §20.4) ──────────────────────────────────────

    def compile_scene_contract(
        self,
        scene: SceneProposal,
        *,
        chapter_no: int,
        scene_no: int,
        states_by_char: dict[str, dict[str, Any]],
        core_anchors_by_char: dict[str, list[dict[str, Any]]] | None = None,
        outline_expected_effects: list[dict[str, Any]] | None = None,
    ) -> SceneContract:
        events = self._renumber_events(scene, chapter_no, scene_no)
        key_map = {}
        for ev, orig in zip(events, scene.provisional_events):
            if orig.event_key:
                key_map[orig.event_key] = ev.event_key
        edges = self._remap_edges(scene.causal_edges, key_map)
        # also remap belief/intention source keys
        belief_deltas = [
            BeliefDelta.model_validate(
                {
                    **b.model_dump(by_alias=True),
                    "source_event_keys": [
                        key_map.get(k, k) for k in (b.source_event_keys or [])
                    ],
                }
            )
            for b in scene.belief_deltas
        ]

        # preconditions: derived from belief "before" values when available
        preconditions = self._derive_preconditions(scene, belief_deltas, states_by_char)

        # belief deltas completed with current values from state
        belief_deltas = self._complete_belief_deltas(belief_deltas, states_by_char)

        # appraisals: compute when proposal supplies event features; keep given ones
        appraisals = self._compile_appraisals(scene, events, states_by_char)

        # affect transitions via appraisal engine where appraisal exists
        affect_transitions = self._compile_affect(
            scene, appraisals, states_by_char, key_map
        )

        # expected effects: hard effects from events + outline expected_state_changes
        expected_effects = self._compile_expected_effects(
            events, outline_expected_effects or [], key_map
        )

        perceptions = self._derive_perceptions(events, scene)
        relevant_ids = self.scene_characters(scene, states_by_char)

        exit_state: dict[str, Any] = {}
        if scene.exit_state:
            exit_state["summary"] = scene.exit_state
        for b in belief_deltas:
            exit_state[f"beliefs.{b.belief_key}"] = b.after
        for eff in expected_effects:
            if eff.mode == "hard":
                exit_state[eff.path] = eff.value

        must_not = list(scene.must_not or [])
        contract = SceneContract(
            scene_no=scene_no,
            dramatic_goal=scene.effective_goal(),
            pov_character_id=str(scene.pov_character_id) if scene.pov_character_id else None,
            location_id=scene.location_id or scene.location,
            relevant_entity_ids=relevant_ids,
            preconditions=preconditions,
            provisional_events=events,
            causal_edges=edges,
            perceptions=perceptions,
            belief_deltas=belief_deltas,
            appraisals=appraisals,
            affect_transitions=affect_transitions,
            intentions=scene.intentions,
            expected_effects=expected_effects,
            must_realize=list(scene.must_include or []),
            must_not_assert=must_not,
            exit_state=exit_state,
        )
        contract.contract_hash = contract.compute_hash()
        return contract

    def _derive_preconditions(
        self,
        scene: SceneProposal,
        belief_deltas: list[BeliefDelta],
        states_by_char: dict[str, dict[str, Any]],
    ) -> list[StatePredicate]:
        preds: list[StatePredicate] = []
        for b in belief_deltas:
            state = normalize_state(states_by_char.get(b.character_id) or {})
            found, cur = get_path(state, f"beliefs.{b.belief_key}.confidence")
            if found and b.before is not None:
                preds.append(
                    StatePredicate(
                        path=f"characters.{b.character_id}.beliefs.{b.belief_key}.confidence",
                        op=">=",
                        value=min(b.before, 0.99) if b.before > 0 else 0.0,
                    )
                )
        return preds[:10]

    def _complete_belief_deltas(
        self, deltas: list[BeliefDelta], states_by_char: dict[str, dict[str, Any]]
    ) -> list[BeliefDelta]:
        out: list[BeliefDelta] = []
        for b in deltas:
            state = normalize_state(states_by_char.get(b.character_id) or {})
            found, cur = get_path(state, f"beliefs.{b.belief_key}.confidence")
            before = b.before
            if before is None and found:
                before = _floaty(cur)
            out.append(
                BeliefDelta.model_validate(
                    {**b.model_dump(by_alias=True), "before": before}
                )
            )
        return out

    def _compile_appraisals(
        self,
        scene: SceneProposal,
        events: list[ProvisionalEvent],
        states_by_char: dict[str, dict[str, Any]],
    ) -> list[CharacterAppraisal]:
        out: list[CharacterAppraisal] = []
        features_by_event: dict[str, dict[str, Any]] = {}
        for ev in events:
            notes = ev.model_extra or {}
            if isinstance(notes.get("appraisal_features"), dict):
                features_by_event[ev.event_key] = notes["appraisal_features"]
        # explicit appraisals in proposal (model_dump preserves extras)
        extras = scene.model_extra or {}
        proposal_appraisals = extras.get("appraisals") or []
        seen: set[tuple[str, str | None]] = set()
        for ap in proposal_appraisals:
            if not isinstance(ap, dict):
                continue
            cid = str(ap.get("character_id", ""))
            ekey = ap.get("event_key")
            feats = {k: v for k, v in ap.items() if k not in ("character_id", "event_key")}
            state = states_by_char.get(cid) or {}
            app = self.appraisal.compute_appraisal(cid, ekey, feats, state)
            # keep explicit values that the engine would have derived
            out.append(app)
            seen.add((cid, ekey))
        # auto-derive for belief-delta characters with source events
        for b in scene.belief_deltas:
            cid = str(b.character_id)
            for src in b.source_event_keys:
                if (cid, src) in seen:
                    continue
                feats = features_by_event.get(src, {})
                congruence = -1.0 * abs(b.after - (b.before or 0.0)) * 2
                feats = {
                    "goal_congruence": congruence if b.polarity < 0 else abs(congruence),
                    "novelty": 0.5,
                    "certainty": abs(b.after),
                    "information_gain": abs(b.after - (b.before or 0.0)),
                    **feats,
                }
                state = states_by_char.get(cid) or {}
                out.append(self.appraisal.compute_appraisal(cid, src, feats, state))
                seen.add((cid, src))
        return out

    def _compile_affect(
        self,
        scene: SceneProposal,
        appraisals: list[CharacterAppraisal],
        states_by_char: dict[str, dict[str, Any]],
        key_map: dict[str, str],
    ) -> list[Any]:
        transitions: list[Any] = []
        extras = scene.model_extra or {}
        proposed = extras.get("affect_transitions") or []
        proposed_by_char = {
            str(t.get("character_id")): t for t in proposed if isinstance(t, dict)
        }
        seen: set[str] = set()
        for ap in appraisals:
            cid = ap.character_id
            if cid in seen or cid in proposed_by_char:
                continue
            state = states_by_char.get(cid) or {}
            shock = "none"
            if ap.attachment_threat > 0.6 or ap.autonomy_threat > 0.6:
                shock = "attachment_threat" if ap.attachment_threat >= ap.autonomy_threat else "goal_damage"
            transitions.append(
                self.appraisal.build_affect_transition(
                    cid,
                    ap,
                    state,
                    cause_event_keys=[ap.event_key] if ap.event_key else [],
                    shock=shock,
                    shock_event_key=ap.event_key,
                )
            )
            seen.add(cid)
        # keep planner-provided transitions (remapped) for other characters
        for cid, t in proposed_by_char.items():
            if cid in seen:
                continue
            transitions.append(t)
            seen.add(cid)
        return transitions

    def _compile_expected_effects(
        self,
        events: list[ProvisionalEvent],
        outline_expected: list[dict[str, Any]],
        key_map: dict[str, str],
    ) -> list[StateDelta]:
        effects: list[StateDelta] = []
        for ev in events:
            for eff in ev.hard_effects:
                effects.append(eff)
        for raw in outline_expected:
            if not isinstance(raw, dict):
                continue
            path = raw.get("path") or raw.get("field")
            if not path:
                continue
            effects.append(
                StateDelta(
                    path=str(path),
                    value=raw.get("value", raw.get("new_value")),
                    mode="hard" if raw.get("mode") == "hard" else "soft",
                    source_event_key=key_map.get(str(raw.get("source_event_key", "")), None)
                    or (str(raw["source_event_key"]) if raw.get("source_event_key") else None),
                )
            )
        return effects

    # ── validate (spec §20.4) ─────────────────────────────────────

    def validate_scene_contract(
        self,
        contract: SceneContract,
        states_by_char: dict[str, dict[str, Any]],
        core_anchor_ids_by_char: dict[str, set[str]] | None = None,
    ) -> ContractValidationReport:
        report = ContractValidationReport()
        anchor_ids: set[str] = set()
        for cid, ids in (core_anchor_ids_by_char or {}).items():
            anchor_ids.update(ids)

        # per-character simulation (state scope = that character)
        for cid in contract.relevant_entity_ids:
            state = states_by_char.get(cid)
            if state is None:
                continue
            scoped = _scope_contract_to_character(contract, cid)
            _, char_report = self.causal.simulate_scene(scoped, state, anchor_ids)
            report.merge(char_report)
        return report

    def hash_contract(self, contract: SceneContract) -> str:
        return contract.compute_hash()


def _scope_contract_to_character(contract: SceneContract, cid: str) -> SceneContract:
    """Filter a contract down to one character's causal slice for simulation."""
    beliefs = [b for b in contract.belief_deltas if b.character_id == cid]
    belief_sources = {b.belief_key: b.source_event_keys for b in beliefs}
    perceptions = [p for p in contract.perceptions if p.character_id == cid]
    intentions = [i for i in contract.intentions if i.character_id == cid]
    appraisals = [a for a in contract.appraisals if a.character_id == cid]
    affects = [a for a in contract.affect_transitions if a.character_id == cid]
    return SceneContract(
        scene_no=contract.scene_no,
        dramatic_goal=contract.dramatic_goal,
        pov_character_id=contract.pov_character_id,
        location_id=contract.location_id,
        relevant_entity_ids=[cid],
        preconditions=contract.preconditions,
        provisional_events=contract.provisional_events,
        causal_edges=contract.causal_edges,
        perceptions=perceptions,
        belief_deltas=beliefs,
        appraisals=appraisals,
        affect_transitions=affects,
        intentions=intentions,
        expected_effects=contract.expected_effects,
        expression_constraints=contract.expression_constraints,
        must_realize=contract.must_realize,
        must_not_assert=contract.must_not_assert,
        exit_state=contract.exit_state,
    )


def _as_id(c: Any) -> str | None:
    if isinstance(c, str):
        return c
    if isinstance(c, dict):
        for k in ("id", "character_id", "entity_id"):
            if c.get(k):
                return str(c[k])
    return None


def _floaty(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
