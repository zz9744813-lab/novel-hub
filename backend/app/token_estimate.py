"""Chinese-safe token estimation (P1 COST-001 / AI v2.0 §9).

Naive `len(text)//4` undercounts Chinese prompts by ~1.6–2.9x vs provider usage.
Until per-model EWMA is available, apply role-aware P95 multipliers + 15% margin.
"""
from __future__ import annotations

import math
import os

# Role multipliers from live NovelForge usage alignment (actual / (len//4)).
# See AI__.md §9.1. Floor at 2.0 for unknown structured roles.
ROLE_P95_RATIO: dict[str, float] = {
    "query_planner": 1.80,
    "evidence_ranker": 1.90,
    "chapter_planner": 1.90,
    "draft_writer": 2.20,
    "review_agent": 3.00,
    "local_rewrite_editor": 2.60,
    "state_extractor": 3.00,
    "patch_editor": 2.60,
    "outline_parser": 2.00,
    "drift_audit": 2.40,
    "aileak_judge": 2.00,
    "memory_compiler": 2.20,
    "reference_analyzer": 2.20,
    "research_planner": 2.00,
    "research_synthesizer": 2.20,
}

DEFAULT_RATIO = float(os.environ.get("TOKEN_ESTIMATE_DEFAULT_RATIO", "3.0"))
MARGIN = float(os.environ.get("TOKEN_ESTIMATE_MARGIN", "1.15"))


def naive_char_tokens(text: str) -> int:
    return max(0, len(text or "") // 4)


def safe_token_estimate(text: str, agent_role: str | None = None) -> int:
    """Return conservative token estimate for budget *recording* (not hard limits)."""
    base = naive_char_tokens(text)
    if base == 0:
        return 0
    ratio = ROLE_P95_RATIO.get(agent_role or "", DEFAULT_RATIO)
    # Never go below 2.0 for CJK-heavy production safety when role known-ish
    if ratio < 2.0 and (agent_role or "") not in ROLE_P95_RATIO:
        ratio = DEFAULT_RATIO
    return int(math.ceil(base * ratio * MARGIN))
