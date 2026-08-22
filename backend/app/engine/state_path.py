"""v9.1 Nested L4 state path operations (spec §10).

Canonical import surface for dotted-path operations over the L4 cognitive
state. ``beliefs.B_may_betray.confidence`` MUST resolve to the nested dict
``{"beliefs": {"B_may_betray": {"confidence": 0.8}}}`` — never a flat key
``"beliefs.B_may_betray.confidence"``.

The implementations live in ``narrative_state``; this module re-exports
them under the spec's API names so callers have one stable surface.
"""
from __future__ import annotations

from app.engine.narrative_state import (
    apply_state_delta,
    apply_state_deltas,
    deep_merge_state,
    delete_path,
    get_path,
    normalize_state,
    set_path,
)


def get_nested(state: dict, path: str):
    """Navigate a dotted path; returns (found, value)."""
    return get_path(state, path)


def set_nested(state: dict, path: str, value) -> bool:
    """Set a dotted path, creating intermediate dicts."""
    return set_path(state, path, value)


def delete_nested(state: dict, path: str) -> bool:
    """Delete a dotted path leaf. True when something was removed."""
    return delete_path(state, path)


__all__ = [
    "get_nested",
    "set_nested",
    "delete_nested",
    "apply_state_delta",
    "apply_state_deltas",
    "deep_merge_state",
    "normalize_state",
]
