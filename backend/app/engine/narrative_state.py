"""v9.0 Narrative state layer (spec §20.1).

Pure-Python operations over the canonical L4 cognitive state.
No LLM calls. No DB writes. Path queries / deep merge / clone / apply delta.

The L4 cognitive schema (spec §7) lives inside MemoryL4StateSnapshot.state JSONB:
    physical / knowledge / beliefs / goals / relationships / affect /
    commitments / misunderstandings / open_questions

Legacy flat states ({"field": ..., "value": ...}) are tolerated.
"""
from __future__ import annotations

import copy
from typing import Any

from app.contracts.narrative import StateDelta, StatePredicate

# Top-level sections of the v9 cognitive state.
COGNITIVE_SECTIONS = (
    "physical",
    "knowledge",
    "beliefs",
    "goals",
    "relationships",
    "affect",
    "commitments",
    "inventory",
    "abilities",
    "misunderstandings",
    "open_questions",
)

# Legacy synonyms unified into `affect` (spec §11)
_AFFECT_ALIASES = ("emotion", "emotion_state", "mood")

_KNOWLEDGE_SOURCES = (
    "saw",
    "heard",
    "was_told",
    "read",
    "inferred_from",
    "remembered",
)


def is_cognitive_state(state: dict[str, Any] | None) -> bool:
    """True when the state already follows the v9 cognitive schema."""
    if not isinstance(state, dict):
        return False
    return any(k in state for k in COGNITIVE_SECTIONS)


def normalize_state(state: dict[str, Any] | None) -> dict[str, Any]:
    """Return a v9-shaped state, wrapping legacy flat dicts under `flat`.

    Affect synonyms (emotion / emotion_state / mood) are unified into
    `affect` (spec §11).
    """
    if not isinstance(state, dict):
        return {}
    if is_cognitive_state(state):
        out = copy.deepcopy(state)
    elif "field" in state and "value" in state and len(state) <= 3:
        # Legacy single-slot dict — preserve under `flat`
        return {"flat": {state["field"]: state["value"]}}
    else:
        out = {"flat": copy.deepcopy(state)}
    for alias in _AFFECT_ALIASES:
        if alias in out:
            payload = out.pop(alias)
            if isinstance(payload, dict) and isinstance(out.get("affect"), dict):
                out["affect"] = deep_merge_state(out["affect"], payload)
            else:
                out["affect"] = payload
    return out


def get_path(state: dict[str, Any], path: str) -> tuple[bool, Any]:
    """Navigate a dotted path. Returns (found, value)."""
    if not path:
        return False, None
    node: Any = state
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, node


def set_path(state: dict[str, Any], path: str, value: Any) -> bool:
    """Set a dotted path, creating intermediate dicts. Returns success."""
    if not path:
        return False
    parts = path.split(".")
    node = state
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    return True


def delete_path(state: dict[str, Any], path: str) -> bool:
    """Delete a dotted path leaf. Returns True when something was removed."""
    if not path:
        return False
    parts = path.split(".")
    node: Any = state
    for part in parts[:-1]:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False
    if isinstance(node, dict) and parts[-1] in node:
        del node[parts[-1]]
        return True
    return False


def compare(op: str, left: Any, right: Any) -> bool:
    try:
        if op == "exists":
            return left is not None
        if op == "not_exists":
            return left is None
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == ">":
            return left is not None and left > right
        if op == ">=":
            return left is not None and left >= right
        if op == "<":
            return left is not None and left < right
        if op == "<=":
            return left is not None and left <= right
    except TypeError:
        return False
    return False


def evaluate_predicate(state: dict[str, Any], pred: StatePredicate) -> bool:
    found, value = get_path(state, pred.path)
    if pred.op in ("exists", "not_exists"):
        return compare(pred.op, value, None)
    if not found:
        return False
    return compare(pred.op, value, pred.value)


# ── deep merge (spec §31) ───────────────────────────────────────────


def deep_merge_state(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge: dicts merge deeply, everything else is overwritten."""
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge_state(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def apply_state_delta(state: dict[str, Any], delta: StateDelta) -> dict[str, Any]:
    """Apply a single StateDelta by dotted path (no more top-level overwrite)."""
    out = copy.deepcopy(state)
    found, old = get_path(out, delta.path)
    if not found:
        old = None
    set_path(out, delta.path, copy.deepcopy(delta.value))
    return out


def apply_state_deltas(state: dict[str, Any], deltas: list[StateDelta]) -> dict[str, Any]:
    out = state
    for d in deltas:
        out = apply_state_delta(out, d)
    return out


def validate_state_path(path: str) -> bool:
    """A legal state path: dotted identifiers, optionally indexing lists."""
    if not path:
        return False
    for part in path.split("."):
        if not part:
            return False
        if not (part.isidentifier() or part.isdigit()):
            return False
    return True


# ── belief / knowledge / goal helpers (spec §7, §8) ─────────────────


def ensure_belief(state: dict[str, Any], belief_key: str, **fields: Any) -> dict[str, Any]:
    beliefs = state.setdefault("beliefs", {})
    entry = beliefs.setdefault(
        belief_key,
        {
            "polarity": 1,
            "confidence": 0.5,
            "source_event_ids": [],
            "last_updated_chapter": None,
        },
    )
    entry.update(fields)
    return state


def ensure_goal(state: dict[str, Any], goal_key: str, **fields: Any) -> dict[str, Any]:
    goals = state.setdefault("goals", {})
    entry = goals.setdefault(
        goal_key,
        {
            "status": "active",
            "priority": 0.5,
            "caused_by_event_ids": [],
            "support_anchor_ids": [],
        },
    )
    entry.update(fields)
    return state


def belief_has_source(state: dict[str, Any], belief_key: str) -> bool:
    entry = (state.get("beliefs") or {}).get(belief_key)
    if not isinstance(entry, dict):
        return False
    return bool(entry.get("source_event_ids"))


def knowledge_has_source(state: dict[str, Any]) -> bool:
    """Every known fact must carry at least one acquisition channel (spec §8)."""
    knowledge = state.get("knowledge")
    if not isinstance(knowledge, dict):
        return True
    sources = knowledge.get("sources")
    if not isinstance(sources, dict):
        return True
    for fact_id, src in sources.items():
        if isinstance(src, dict):
            channel = str(src.get("channel", "")).lower()
        else:
            channel = str(src).lower()
        if channel and channel not in _KNOWLEDGE_SOURCES:
            return False
    return True


def active_goals(state: dict[str, Any]) -> dict[str, dict]:
    goals = state.get("goals")
    if not isinstance(goals, dict):
        return {}
    return {k: v for k, v in goals.items() if isinstance(v, dict) and v.get("status") == "active"}


def active_beliefs(state: dict[str, Any]) -> dict[str, dict]:
    beliefs = state.get("beliefs")
    if not isinstance(beliefs, dict):
        return {}
    return {k: v for k, v in beliefs.items() if isinstance(v, dict)}


def affect_vad(state: dict[str, Any]) -> tuple[float, float, float]:
    vad = (state.get("affect") or {}).get("vad") or {}
    return (
        float(vad.get("valence", 0.0)),
        float(vad.get("arousal", 0.0)),
        float(vad.get("dominance", 0.0)),
    )


def set_affect_vad(state: dict[str, Any], valence: float, arousal: float, dominance: float) -> None:
    affect = state.setdefault("affect", {})
    affect["vad"] = {
        "valence": round(float(valence), 4),
        "arousal": round(float(arousal), 4),
        "dominance": round(float(dominance), 4),
    }


# ── affect profile (spec §11.2) ─────────────────────────────────────

DEFAULT_AFFECT_PROFILE: dict[str, float] = {
    "reactivity": 0.6,
    "recovery_tau": 4.0,
    "suppression": 0.5,
    "impulsivity": 0.4,
    "threat_bias": 0.5,
    "attachment_sensitivity": 0.5,
    "norm_sensitivity": 0.5,
}


def affect_profile(state: dict[str, Any]) -> dict[str, float]:
    raw = (state.get("affect_profile") or {}) if isinstance(state, dict) else {}
    prof = dict(DEFAULT_AFFECT_PROFILE)
    for k, v in raw.items():
        if k in prof:
            try:
                prof[k] = max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                continue
    return prof
