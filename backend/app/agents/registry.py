"""v9.7 RoleRegistry: single source of truth for agent roles (spec §4).

Model AutoConfig, Prompt Studio, frontend labels and quality stats must all
read FROM here — no more scattered hardcoded role lists (spec item 26).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRoleSpec:
    role: str
    display_name: str
    production: bool
    strict: bool
    json_output: bool
    prompt_required: bool
    model_required: bool
    default_quality_floor: float
    expected_context_tokens: int
    model_priority: str  # high|normal|low
    quality_signal_profile: str


ROLE_REGISTRY: dict[str, AgentRoleSpec] = {
    role: AgentRoleSpec(
        role=role,
        display_name=disp,
        production=prod,
        strict=strict,
        json_output=json_out,
        prompt_required=p_req,
        model_required=m_req,
        default_quality_floor=floor,
        expected_context_tokens=ctx,
        model_priority=prio,
        quality_signal_profile=profile,
    )
    for role, (disp, prod, strict, json_out, p_req, m_req, floor, ctx, prio, profile) in {
        "chapter_planner": ("ChapterPlanner", True, True, True, True, True, 80, 96_000, "high", "planner"),
        "draft_writer": ("DraftWriter", True, True, False, True, True, 85, 120_000, "high", "draft"),
        "review_agent": ("ReviewAgent", True, True, False, True, True, 80, 96_000, "high", "review"),
        "state_extractor": ("StateExtractor", True, True, True, True, True, 80, 64_000, "high", "state"),
        "style_analyzer": ("StyleAnalyzer", True, True, True, True, True, 75, 48_000, "normal", "style"),
        "blank_planner": ("BlankPlanner", True, True, True, True, True, 70, 96_000, "normal", "planner"),
        "query_planner": ("QueryPlanner", True, False, True, True, True, 70, 64_000, "normal", "retrieval"),
        "outline_parser": ("OutlineParser", True, True, True, True, True, 70, 96_000, "normal", "planner"),
        "drift_audit": ("DriftAudit", True, False, True, True, True, 70, 96_000, "low", "state"),
        "memory_compiler": ("MemoryCompiler", True, False, True, True, True, 70, 64_000, "low", "state"),
        "local_rewrite_editor": ("LocalRewriteEditor", True, True, True, True, True, 75, 32_000, "normal", "patch"),
        "evidence_ranker": ("EvidenceRanker", True, True, True, True, True, 70, 64_000, "normal", "retrieval"),
        "ai_tone_lint": ("AIToneLint", False, False, True, False, False, 0, 16_000, "low", "style"),
    }.items()
}

STRICT_ROLES = frozenset(r for r, spec in ROLE_REGISTRY.items() if spec.strict)
REQUIRED_PROD_MODEL_ROLES = frozenset(
    r for r, spec in ROLE_REGISTRY.items() if spec.production and spec.model_required
)


def required_roles() -> list[str]:
    """Production roles that need a model route (AutoConfigure target, spec §11)."""
    return sorted(REQUIRED_PROD_MODEL_ROLES)


def validate_all(registered: set[str]) -> list[str]:
    """Return missing role keys that must exist elsewhere (readiness check)."""
    return sorted(r for r in REQUIRED_PROD_MODEL_ROLES if r not in registered)
