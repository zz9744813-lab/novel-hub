"""Model autopilot: capability handling (spec §7–§10, §32).

Capability facts per model come from: manual lock > provider metadata > probe >
family seed > unknown. Context window is never guessed from the model name.
"""
from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import ROLE_REGISTRY
from app.model_eval.suite_definitions import ROUTABLE_ROLES, qualification_role_for
from app.models import ModelCapabilityProfile, ModelCatalog

logger = logging.getLogger("novelforge.model_autopilot.capability")

# Key roles whose auto routing REQUIRES a known context window (spec §32).
_DIRECT_CONTEXT_REQUIRED_ROLES = {
    "chapter_planner",
    "draft_writer",
    "review_agent",
    "state_extractor",
}
CONTEXT_REQUIRED_ROLES = {
    role
    for role in ROUTABLE_ROLES
    if qualification_role_for(role) in _DIRECT_CONTEXT_REQUIRED_ROLES
}

DEFAULT_ROLE_QUALITY_FLOOR = {
    role: float(spec.default_quality_floor) for role, spec in ROLE_REGISTRY.items()
}


def context_fit_score(context_window: int | None, required_context: int) -> float | None:
    """Context fit (spec §33). Returns None => reject (insufficient/unknown)."""
    if context_window is None or required_context is None:
        return None
    if context_window < required_context:
        return None
    utilization = required_context / context_window
    if utilization > 0.95:
        return None
    if utilization <= 0.50:
        return 100.0
    if utilization <= 0.70:
        return 90.0
    if utilization <= 0.85:
        return 70.0
    return 40.0


def required_context_for(
    estimated_input_tokens: int, reserved_output_tokens: int, context_window: int | None
) -> int:
    """required_context = input + output + safety margin (spec §31)."""
    margin = max(4096, int(context_window or 0) * 5 // 100)
    return int(estimated_input_tokens) + int(reserved_output_tokens) + margin


async def apply_manual_capability(
    db: AsyncSession,
    catalog: ModelCatalog,
    *,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
    supports_json_schema: bool | None = None,
    supports_reasoning: bool | None = None,
    quality_tier: str | None = None,
    static_quality_score: float | None = None,
) -> ModelCapabilityProfile:
    """Manual capability override; writes ModelChangeLog via caller (spec §70)."""
    profile = (
        await db.execute(
            select(ModelCapabilityProfile).where(
                ModelCapabilityProfile.model_catalog_id == catalog.id
            )
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = ModelCapabilityProfile(
            id=uuid4(), model_catalog_id=catalog.id, capability_source="manual"
        )
        db.add(profile)
        await db.flush()
    if context_window is not None:
        profile.context_window = context_window
    if max_output_tokens is not None:
        profile.max_output_tokens = max_output_tokens
    if supports_json_schema is not None:
        profile.supports_json_schema = supports_json_schema
    if supports_reasoning is not None:
        profile.supports_reasoning = supports_reasoning
    if quality_tier is not None:
        profile.quality_tier = quality_tier
    if static_quality_score is not None:
        profile.static_quality_score = static_quality_score
    profile.capability_source = "manual"
    return profile
