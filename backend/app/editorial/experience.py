"""v9.3 Experience Cards engine (spec §33–§40, PR-06).

The editorial "错题本": turns aggregated feedback insights into
generalized, retrievable rules that get injected into generation prompts.

Lifecycle: candidate → active → locked | superseded | rejected.

Key operations:
* ``synthesize_cards_from_review`` — merge/dedupe one review round's
  insights into cards (same category+component and similar instruction →
  bump support_count instead of a duplicate card).
* ``retrieve_cards`` — deterministic scored retrieval by scope,
  chapter range and trigger conditions.
* ``render_cards_for_prompt`` — compact instruction block for the draft
  writer / patch editor prompts.
"""
from __future__ import annotations

import difflib
import logging
import math
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import (
    EditorialAnnotation,
    EditorialExperienceCard,
    EditorialFeedbackInsight,
    EditorialReviewPolicy,
    EditorialReviewRound,
)

logger = logging.getLogger("novelforge.editorial_experience")

SIMILARITY_MERGE_THRESHOLD = 0.55
MIN_SUPPORT_AUTO_ACTIVE = 2  # candidate auto-activates at this support when policy allows

RULE_TYPE_BY_ANNOTATION = {
    "direct_edit": "preference",
    "preference": "preference",
    "forbidden_pattern": "anti_pattern",
    "praise": "positive_pattern",
    "suggestion": "preference",
    "issue": "anti_pattern",
}

TARGET_COMPONENTS = {
    "chapter_planner": ["chapter_planner"],
    "ccne": ["chapter_planner", "draft_writer"],
    "memory": ["context_builder"],
    "voice": ["draft_writer", "patch_editor"],
    "style": ["draft_writer", "patch_editor"],
    "draft_writer": ["draft_writer"],
    "review_agent": ["review_agent"],
    "patch_editor": ["patch_editor"],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a[:300], b[:300]).ratio()


def _card_instruction(ann: EditorialAnnotation, insight: EditorialFeedbackInsight | None) -> str:
    """Generalize one annotation into a rule sentence (category-specific templates)."""
    if insight is not None:
        category = ann.category or insight.normalized_category
    else:
        category = ann.category
    category = category or "other"
    comment = (ann.comment or "").strip()
    templates = {
        "dialogue": "对白：{c}",
        "character": "人物：{c}",
        "voice": "语气：{c}",
        "style": "文风：{c}",
        "prose": "行文：{c}",
        "pacing": "节奏：{c}",
        "description": "描写：{c}",
        "plot": "剧情：{c}",
        "structure": "结构：{c}",
        "causality": "因果：{c}",
        "continuity": "连贯：{c}",
        "consistency": "一致性：{c}",
        "worldbuilding": "世界观：{c}",
        "immersion": "代入感：{c}",
    }
    if ann.annotation_type == "forbidden_pattern":
        return f"禁止出现：{comment or ann.quoted_text or '该模式'}"
    if ann.annotation_type == "praise":
        return f"保持优点：{comment or '该写法有效'}"
    prefix = templates.get(category, f"{category}：")
    return prefix.format(c=comment or ann.quoted_text or "（无评论）")[:500]


async def synthesize_cards_from_review(db: AsyncSession, review_round_id) -> dict:
    """Merge one submitted round's annotations+insights into experience cards."""
    rnd = (
        await db.execute(
            select(EditorialReviewRound).where(EditorialReviewRound.id == review_round_id)
        )
    ).scalar_one_or_none()
    if rnd is None:
        return {"status": "round_not_found"}

    annotations = list(
        (
            await db.execute(
                select(EditorialAnnotation).where(EditorialAnnotation.review_round_id == rnd.id)
            )
        ).scalars()
    )
    insight_by_ann = {
        i.annotation_id: i
        for i in (
            await db.execute(
                select(EditorialFeedbackInsight).where(
                    EditorialFeedbackInsight.annotation_id.in_([a.id for a in annotations])
                )
            )
        ).scalars()
    } if annotations else {}

    existing_cards = list(
        (
            await db.execute(
                select(EditorialExperienceCard).where(
                    EditorialExperienceCard.book_id == rnd.book_id,
                    EditorialExperienceCard.status.in_(["candidate", "active"]),
                )
            )
        ).scalars()
    )

    created = 0
    merged = 0
    for ann in annotations:
        if ann.annotation_type == "question":
            continue
        insight = insight_by_ann.get(ann.id)
        category = (ann.category or (insight.normalized_category if insight else None)) or "other"
        instruction = _card_instruction(ann, insight)
        rule_type = RULE_TYPE_BY_ANNOTATION.get(ann.annotation_type, "preference")
        component = insight.root_cause_component if insight else "draft_writer"

        # dedupe/merge against existing cards of the same category+component
        best, best_sim = None, 0.0
        for card in existing_cards:
            if card.category != category or card.is_locked:
                continue
            sim = _similarity(card.instruction, instruction)
            if sim > best_sim:
                best, best_sim = card, sim

        if best is not None and best_sim >= SIMILARITY_MERGE_THRESHOLD:
            best.support_count += 1
            best.last_confirmed_at = _now()
            best.confidence = min(0.95, 0.5 + 0.1 * math.log1p(best.support_count))
            src = list(best.source_annotation_ids or [])
            src.append(str(ann.id))
            best.source_annotation_ids = src
            merged += 1
            continue

        card = EditorialExperienceCard(
            book_id=rnd.book_id,
            rule_type=rule_type,
            scope_type="book",
            scope_ref={"chapter": None},
            category=category,
            trigger_conditions={"category": category, "component": component},
            instruction=instruction,
            rationale=(ann.comment or "")[:500] or None,
            avoid_when=[] if ann.annotation_type != "praise" else ["该模式不适用时"],
            target_components=TARGET_COMPONENTS.get(component, ["draft_writer"]),
            positive_example_refs=[str(ann.id)] if ann.annotation_type == "praise" else [],
            negative_example_refs=[str(ann.id)] if ann.annotation_type != "praise" else [],
            support_count=1,
            contradiction_count=0,
            confidence=0.5,
            status="candidate",
            is_locked=False,
            effective_from_chapter=None,
            last_confirmed_at=_now(),
            source_annotation_ids=[str(ann.id)],
        )
        db.add(card)
        existing_cards.append(card)
        created += 1

    await db.flush()

    # policy-gated auto-activation: mature candidates become active
    policy = (
        await db.execute(
            select(EditorialReviewPolicy).where(EditorialReviewPolicy.book_id == rnd.book_id)
        )
    ).scalar_one_or_none()
    activated = 0
    if policy is not None and policy.experience_auto_activation:
        for card in existing_cards:
            if card.status == "candidate" and card.support_count >= MIN_SUPPORT_AUTO_ACTIVE:
                card.status = "active"
                activated += 1

    await db.commit()
    logger.info(
        "experience synthesis round %s: %d created, %d merged, %d auto-activated",
        review_round_id,
        created,
        merged,
        activated,
    )
    return {
        "status": "ok",
        "created": created,
        "merged": merged,
        "auto_activated": activated,
    }


def _scope_score(card: EditorialExperienceCard, book_id) -> float:
    if card.scope_type == "global":
        return 0.15
    if card.book_id is None:
        return 0.1
    if str(card.book_id) == str(book_id):
        return 0.25 if card.scope_type == "book" else 0.35
    return -1.0  # other book → excluded


async def retrieve_cards(
    db: AsyncSession,
    book_id,
    chapter_no: int | None = None,
    scene_type: str | None = None,
    character_ids: list[str] | None = None,
    include_candidates: bool = False,
    limit: int = 8,
) -> list[EditorialExperienceCard]:
    """Deterministic scored retrieval: scope → support → confidence."""
    statuses = ["active"] if not include_candidates else ["active", "candidate"]
    cards = list(
        (
            await db.execute(
                select(EditorialExperienceCard).where(
                    EditorialExperienceCard.status.in_(statuses)
                )
            )
        ).scalars()
    )

    scored: list[tuple[float, EditorialExperienceCard]] = []
    for card in cards:
        scope = _scope_score(card, book_id)
        if scope < 0:
            continue
        if (
            card.effective_from_chapter is not None
            and chapter_no is not None
            and chapter_no < card.effective_from_chapter
        ):
            continue
        trig = card.trigger_conditions or {}
        bonus = 0.0
        if scene_type and trig.get("scene_type") == scene_type:
            bonus += 0.4
        if character_ids and str(trig.get("character_id")) in character_ids:
            bonus += 0.3
        score = (
            scope
            + bonus
            + 0.3 * math.log1p(card.support_count)
            + 0.5 * (card.confidence or 0.5)
            - 0.4 * math.log1p(card.contradiction_count)
        )
        scored.append((score, card))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for _, c in scored[:limit]]


def render_cards_for_prompt(cards: list[EditorialExperienceCard]) -> str:
    """Compact, numbered instruction block for generation prompts."""
    if not cards:
        return ""
    lines = ["<写作经验>（人工编辑审校沉淀，必须遵守）"]
    for i, card in enumerate(cards, 1):
        scope = {"global": "全局", "book": "本书", "character": "角色", "scene_type": "场景"}.get(
            card.scope_type, card.scope_type
        )
        lines.append(f"{i}. [{scope}·{card.category}] {card.instruction}")
    lines.append("</写作经验>")
    return "\n".join(lines)


async def set_card_status(db: AsyncSession, card_id, status: str, lock: bool | None = None) -> EditorialExperienceCard | None:
    """Human lifecycle control: activate / reject / lock / supersede."""
    card = (
        await db.execute(
            select(EditorialExperienceCard).where(EditorialExperienceCard.id == card_id)
        )
    ).scalar_one_or_none()
    if card is None:
        return None
    if status not in {"candidate", "active", "locked", "superseded", "rejected"}:
        raise ValueError("INVALID_CARD_STATUS")
    card.status = status
    if lock is not None:
        card.is_locked = lock
    if status == "active":
        card.last_confirmed_at = _now()
    await db.commit()
    return card
