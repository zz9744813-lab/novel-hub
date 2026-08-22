"""v9.1 Per-scene relevant state selector (spec §6).

Selects only the state slices a scene actually needs:
- characters actually present in the scene (actors, involves, POV, belief/intent owners)
- beliefs referenced by the scene's belief deltas or support pools
- active goals (status == active) up to a small cap
- affect / physical / relationships slices for those characters only
- core anchors for those characters
- knowledge boundary + causal frontier from the contract

Pure function — no DB, no LLM. The pipeline calls this per scene and feeds
the result to ContextAssembler so no scene receives the whole chapter's state.
"""
from __future__ import annotations

from typing import Any

from app.contracts.narrative import SceneContract
from app.engine.narrative_state import normalize_state

_MAX_BELIEFS = 8
_MAX_GOALS = 5


def _scene_character_ids(contract: SceneContract | dict[str, Any]) -> set[str]:
    """Characters the scene actually touches."""
    ids: set[str] = set()

    def _add(v: Any) -> None:
        if v is None:
            return
        if isinstance(v, str):
            ids.add(v)
        elif isinstance(v, dict):
            for k in ("id", "character_id", "entity_id"):
                if v.get(k):
                    ids.add(str(v[k]))
                    break

    if isinstance(contract, SceneContract):
        if contract.pov_character_id:
            ids.add(str(contract.pov_character_id))
        for ev in contract.provisional_events:
            if ev.actor_id:
                ids.add(str(ev.actor_id))
            for i in ev.involves or []:
                ids.add(str(i))
        for b in contract.belief_deltas:
            ids.add(str(b.character_id))
        for it in contract.intentions:
            ids.add(str(it.character_id))
        for p in contract.perceptions:
            ids.add(str(p.character_id))
        for a in contract.appraisals:
            ids.add(str(a.character_id))
        for t in contract.affect_transitions:
            ids.add(str(t.character_id))
        for e in contract.expression_constraints:
            ids.add(str(e.character_id))
    else:
        if contract.get("pov_character_id"):
            ids.add(str(contract["pov_character_id"]))
        for ev in contract.get("provisional_events") or []:
            if not isinstance(ev, dict):
                continue
            if ev.get("actor_id"):
                ids.add(str(ev["actor_id"]))
            for i in ev.get("involves") or []:
                ids.add(str(i))
        for b in contract.get("belief_deltas") or []:
            if isinstance(b, dict) and b.get("character_id"):
                ids.add(str(b["character_id"]))
        for it in contract.get("intentions") or []:
            if isinstance(it, dict) and it.get("character_id"):
                ids.add(str(it["character_id"]))
        for p in contract.get("perceptions") or []:
            if isinstance(p, dict) and p.get("character_id"):
                ids.add(str(p["character_id"]))
    ids.discard("")
    return ids


def _select_beliefs(state: dict[str, Any], wanted: set[str] | None) -> dict[str, Any]:
    beliefs = state.get("beliefs")
    if not isinstance(beliefs, dict):
        return {}
    if wanted:
        picked = {k: v for k, v in beliefs.items() if k in wanted}
        if picked:
            return picked
    # fall back to strongest-confidence beliefs
    def _conf(v: Any) -> float:
        if isinstance(v, dict):
            try:
                return abs(float(v.get("confidence") or 0))
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    ranked = sorted(beliefs.items(), key=lambda kv: _conf(kv[1]), reverse=True)
    return dict(ranked[:_MAX_BELIEFS])


def _select_goals(state: dict[str, Any]) -> dict[str, Any]:
    goals = state.get("goals")
    if not isinstance(goals, dict):
        return {}

    def _active(v: Any) -> bool:
        return not (isinstance(v, dict) and v.get("status") not in (None, "active"))

    def _prio(v: Any) -> float:
        if isinstance(v, dict):
            try:
                return float(v.get("priority") or 0)
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    active = {k: v for k, v in goals.items() if _active(v)}
    ranked = sorted(active.items(), key=lambda kv: _prio(kv[1]), reverse=True)
    return dict(ranked[:_MAX_GOALS])


def select_relevant_scene_state(
    *,
    scene_contract: SceneContract | dict[str, Any] | None,
    l4_states: dict[str, dict[str, Any]],
    core_anchors_by_char: dict[str, list[dict[str, Any]]] | None = None,
    max_characters: int = 8,
    max_beliefs_per_character: int = _MAX_BELIEFS,
    max_goals_per_character: int = 5,
    causal_depth: int = 3,
) -> dict[str, Any]:
    """Return only the state slices relevant to one scene (spec §6)."""
    empty = {
        "characters": {},
        "knowledge_boundary": {},
        "causal_frontier": {},
        "character_ids": [],
    }
    if scene_contract is None:
        return empty

    contract = scene_contract
    if isinstance(contract, dict):
        try:
            contract = SceneContract.model_validate(contract)
        except Exception:
            return empty

    scene_ids = _scene_character_ids(contract)
    if not scene_ids:
        return empty

    wanted_beliefs: set[str] = set()
    if isinstance(scene_contract, SceneContract):
        wanted_beliefs = {b.belief_key for b in scene_contract.belief_deltas}
        for it in scene_contract.intentions:
            wanted_beliefs.update(it.support_belief_keys or [])
    else:
        for b in scene_contract.get("belief_deltas") or []:
            if isinstance(b, dict) and b.get("belief_key"):
                wanted_beliefs.add(str(b["belief_key"]))

    # prioritize characters the scene touches; cap at max_characters
    ordered = [c for c in scene_ids if c in l4_states]
    ordered += [c for c in scene_ids if c not in l4_states]
    selected_ids = ordered[:max_characters]

    characters: dict[str, Any] = {}
    for cid in selected_ids:
        raw = l4_states.get(cid)
        if not isinstance(raw, dict):
            continue
        state = normalize_state(raw)
        beliefs = _select_beliefs(state, wanted_beliefs)
        beliefs = dict(list(beliefs.items())[:max_beliefs_per_character])
        goals = dict(list(_select_goals(state).items())[:max_goals_per_character])
        characters[cid] = {
            "beliefs": beliefs,
            "goals": goals,
            "affect": state.get("affect") or {},
            "physical": state.get("physical") or {},
            "relationships": state.get("relationships") or {},
            "core_anchors": (core_anchors_by_char or {}).get(cid, []),
        }

    knowledge_boundary: dict[str, list[str]] = {}
    for p in contract.perceptions:
        knowledge_boundary.setdefault(str(p.character_id), []).append(
            f"{p.event_key}:{p.channel}"
        )

    causal_frontier: list[dict[str, Any]] = []
    key_by_event = {e.event_key: e for e in contract.provisional_events}
    seen: set[str] = set()
    # seeds: events that other events depend on (hard-edge sources)
    for edge in contract.causal_edges:
        if edge.mode != "hard":
            continue
        if edge.from_key in key_by_event and edge.from_key not in seen:
            seen.add(edge.from_key)
            ev = key_by_event[edge.from_key]
            causal_frontier.append(
                {
                    "event_key": ev.event_key,
                    "action": ev.action,
                    "event_type": ev.event_type,
                    "actor_id": ev.actor_id,
                    "depth": 0,
                }
            )
    causal_frontier = causal_frontier[: 2 * causal_depth]

    return {
        "characters": characters,
        "knowledge_boundary": knowledge_boundary,
        "causal_frontier": causal_frontier,
        "character_ids": selected_ids,
    }
