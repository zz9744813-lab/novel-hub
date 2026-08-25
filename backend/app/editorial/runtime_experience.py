"""v9.7 Experience Runtime Injection (spec §5): cards actually reach the next
chapter's context package — scored, scoped, and bounded (3–8 per call).
"""
from __future__ import annotations

import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EditorialExperienceCard

# agent_role → allowed card rule types (spec §5)
ROLE_RULE_TYPES = {
    "chapter_planner": ["planning_rule", "character_rule", "scene_mode_rule"],
    "draft_writer": ["positive_pattern", "anti_pattern", "style_rule", "dialogue_preference", "character_rule"],
    "review_agent": ["historic_miss", "review_rule", "forbidden_pattern"],
    "patch_editor": ["direct_edit_preference", "paragraph_local_repair", "anti_pattern"],
    "style_analyzer": ["style_rule"],
    "local_rewrite": ["positive_pattern", "anti_pattern"],
}


async def build_experience_context(
    db: AsyncSession,
    *,
    book_id,
    agent_role: str,
    chapter_no: int | None = None,
    scene_type: str | None = None,
    character_ids: list[uuid.UUID] | None = None,
    categories: list[str] | None = None,
    limit: int = 6,
) -> list[dict]:
    """Score and select relevant cards per spec §5 ordering.

    Order: locked > book-local active > scene exact > character exact >
    category match > support_count > confidence > recent confirmation.
    Returns card refs (never the raw content) for the context package.
    """
    allowed = ROLE_RULE_TYPES.get(agent_role)
    if allowed is None:
        return []

    cards = (
        (
            await db.execute(
                select(EditorialExperienceCard).where(
                    EditorialExperienceCard.book_id == book_id,
                    EditorialExperienceCard.status.in_(("active", "locked")),
                )
            )
        )
        .scalars()
        .all()
    )
    scored = []
    for card in cards:
        if card.rule_type not in allowed:
            continue
        score = 0.0
        reasons = []
        if card.status == "locked":
            score += 100
            reasons.append("locked")
        elif card.status == "active":
            score += 50
            reasons.append("active")
        if card.scope_type == "book":
            score += 20
        if (
            scene_type
            and card.scope_type == "scene_type"
            and (card.scope_ref or {}).get("scene_type") == scene_type
        ):
            score += 15
        if (
            character_ids
            and card.scope_type == "character"
            and (card.scope_ref or {}).get("character_id") in {str(c) for c in character_ids}
        ):
            score += 10
        if categories and card.category in categories:
            score += 8
        score += min(20, (card.support_count or 0) * 0.5)
        score += (card.confidence or 0) * 10
        scored.append((score, card))

    scored.sort(key=lambda pair: -pair[0])
    selected = scored[:limit]
    return [
        {
            "card_id": str(card.id),
            "rule_type": card.rule_type,
            "scope_type": card.scope_type or "book",
            "score": round(score, 2),
            # prompt-safe content: instruction + guard rails only (no ids/links)
            "instruction": card.instruction,
            "avoid_when": (card.avoid_when or [])[:4],
            "rationale": card.rationale,
        }
        for score, card in selected
    ]
