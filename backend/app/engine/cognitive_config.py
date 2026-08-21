"""v9.0 Cognitive-Causal engine configuration (spec §48).

All values are narrative simulation parameters, project-level overridable.
"""
from __future__ import annotations

from typing import Any

DEFAULT_CAUSAL_CONFIG: dict[str, Any] = {
    # Affect dynamics (spec §12)
    "affect_jump_minor": 0.35,   # VAD jump threshold without shock, minor scene
    "affect_jump_major": 0.55,   # threshold for major scene
    "major_goal_priority": 0.7,  # goals >= this are "major"
    "major_relationship_delta": 0.35,
    # Counterfactual audit (spec §33)
    "counterfactual_enabled": True,
    "counterfactual_key_nodes_only": True,
    # Relevant state selection (spec §21)
    "max_causal_frontier_hops": 3,
    "max_relevant_beliefs_per_scene": 8,
    "max_core_anchors_per_scene": 5,
    "max_relevant_events_per_scene": 8,
    # Attribution policy (spec §6.3)
    "unresolved_major_is_review_issue": True,
    "unresolved_pivotal_blocks": True,
    # L4 cognitive defaults (spec §7)
    "default_belief_confidence": 0.5,
    "default_recovery_tau": 4.0,
}

# Per-book overrides may be stored in book_settings under this key.
COGNITIVE_SETTINGS_KEY = "cognitive_engine_config"


def load_cognitive_config(book_settings: dict[str, str] | None = None) -> dict[str, Any]:
    """Merge defaults with project-level overrides from book_settings."""
    cfg = dict(DEFAULT_CAUSAL_CONFIG)
    if book_settings:
        raw = book_settings.get(COGNITIVE_SETTINGS_KEY)
        if raw:
            import json

            try:
                overrides = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(overrides, dict):
                    for k, v in overrides.items():
                        if k in cfg:
                            cfg[k] = v
            except Exception:
                pass
    return cfg
