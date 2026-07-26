"""ChapterPlannerAgent - expands outline node into beat sheet + scene plan.
Per §7.2 Step 4 + §A.2 v7.3.

P0-03: short sessions — load DTOs, then call_agent without holding Session.
Deterministic fallback scene plan when LLM returns non-JSON / missing scenes.
"""
from __future__ import annotations

import uuid
import json
import re
import logging
from dataclasses import dataclass, field
from sqlalchemy import select
from app.database import async_session_factory
from app.agents.caller import call_agent
from app.models import (
    OutlineNode,
    MemoryL2StageSummary,
    MemoryL3VolumeSummary,
    StyleVoiceCard,
    StyleToneAnchor,
)

logger = logging.getLogger("novelforge.chapter_planner")


@dataclass(frozen=True)
class ChapterPlanInput:
    book_id: uuid.UUID
    chapter_id: uuid.UUID
    outline: dict
    forced_dependencies: list
    l4_states: dict
    l2_summary: dict
    l3_summary: dict
    voice_cards: list
    tone_anchor: dict
    retrieved_evidence: list = field(default_factory=list)
    target_word_count: int = 3000


def _deterministic_plan(outline: dict, target_word_count: int = 3000) -> dict:
    """Always-valid scene plan from outline beats so drafting can proceed."""
    goal = outline.get("goal") or "推进本章剧情"
    beats = outline.get("required_beats") or []
    if isinstance(beats, str):
        beats = [beats]
    if not beats:
        beats = [
            "开场：建立场景与人物状态",
            "发展：冲突升级或关键信息出现",
            "收束：本章目标落地并留下钩子",
        ]
    # Cap scenes to 3 for VPS latency
    beats = list(beats)[:3]
    while len(beats) < 3:
        beats.append(f"补充节拍{len(beats)+1}：围绕目标推进")
    per = max(800, int(target_word_count / len(beats)))
    scenes = []
    for i, beat in enumerate(beats, start=1):
        scenes.append(
            {
                "scene_no": i,
                "goal": str(beat)[:500],
                "pov_character_id": None,
                "location": None,
                "target_word_count": per,
                "must_include": [goal] if i == 1 else [],
                "must_not": outline.get("forbidden_outcomes") or [],
            }
        )
    return {
        "chapter_goal": goal,
        "scenes": scenes,
        "source": "deterministic_fallback",
    }


def _coerce_plan(result, meta: dict | None = None) -> dict | None:
    if isinstance(result, dict) and isinstance(result.get("scenes"), list) and result["scenes"]:
        return result
    text = ""
    if isinstance(result, str):
        text = result
    elif isinstance(result, dict):
        for k in ("raw", "final_content", "content"):
            if isinstance(result.get(k), str):
                text = result[k]
                break
    if meta and not text and isinstance(meta.get("raw"), str):
        text = meta["raw"]
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict) and obj.get("scenes"):
                return obj
        except Exception:
            pass
    m = re.search(r"\{.*\"scenes\"\s*:\s*\[.*\].*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict) and obj.get("scenes"):
                return obj
        except Exception:
            pass
    return None


async def load_chapter_plan_input(
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    outline_node_id: uuid.UUID,
    forced_dependencies: list[dict],
    l4_states: dict,
    retrieved_evidence: list | None = None,
    target_word_count: int = 3000,
) -> ChapterPlanInput:
    async with async_session_factory() as db:
        outline_node = (
            await db.execute(select(OutlineNode).where(OutlineNode.id == outline_node_id))
        ).scalar_one_or_none()
        if not outline_node:
            raise ValueError(f"outline_node {outline_node_id} not found")

        l2 = await db.execute(
            select(MemoryL2StageSummary)
            .where(MemoryL2StageSummary.book_id == book_id)
            .order_by(MemoryL2StageSummary.chapter_range_end.desc())
            .limit(1)
        )
        l2_summary = l2.scalar_one_or_none()

        l3 = await db.execute(
            select(MemoryL3VolumeSummary)
            .where(MemoryL3VolumeSummary.book_id == book_id)
            .order_by(MemoryL3VolumeSummary.volume_no.desc())
            .limit(1)
        )
        l3_summary = l3.scalar_one_or_none()

        vc = await db.execute(select(StyleVoiceCard).where(StyleVoiceCard.book_id == book_id))
        voice_cards = [
            {
                "character_id": str(v.character_id),
                "register": v.register,
                "emotion_expression": v.emotion_expression,
            }
            for v in vc.scalars().all()
        ]

        ta = await db.execute(
            select(StyleToneAnchor)
            .where(StyleToneAnchor.book_id == book_id)
            .order_by(StyleToneAnchor.version.desc())
            .limit(1)
        )
        tone_anchor = ta.scalar_one_or_none()
        tone_dict = (
            {
                "narrative_pov": tone_anchor.narrative_pov,
                "emotional_temperature": tone_anchor.emotional_temperature,
                "pacing": tone_anchor.pacing,
            }
            if tone_anchor
            else {}
        )

        outline = {
            "chapter_no": outline_node.chapter_no,
            "title": outline_node.title,
            "goal": outline_node.goal,
            "required_beats": outline_node.required_beats,
            "forbidden_outcomes": outline_node.forbidden_outcomes,
            "depends_on": outline_node.depends_on,
            "expected_state_changes": outline_node.expected_state_changes,
            "involved_character_ids": outline_node.involved_character_ids,
        }

    return ChapterPlanInput(
        book_id=book_id,
        chapter_id=chapter_id,
        outline=outline,
        forced_dependencies=forced_dependencies,
        l4_states=l4_states,
        l2_summary=l2_summary.summary_json if l2_summary else {},
        l3_summary=l3_summary.summary_json if l3_summary else {},
        voice_cards=voice_cards,
        tone_anchor=tone_dict,
        retrieved_evidence=retrieved_evidence or [],
        target_word_count=target_word_count,
    )


async def generate_chapter_plan(plan_input: ChapterPlanInput) -> dict | None:
    user_content = json.dumps(
        {
            "instruction": (
                "Return ONLY JSON with key scenes: array of objects "
                "{scene_no, goal, target_word_count}. No prose."
            ),
            "chapter_outline_node": plan_input.outline,
            "forced_dependencies": plan_input.forced_dependencies,
            "l4_state": plan_input.l4_states,
            "l2_summary": plan_input.l2_summary,
            "l3_summary": plan_input.l3_summary,
            "event_and_retrieved_evidence": plan_input.retrieved_evidence,
            "voice_cards": plan_input.voice_cards,
            "tone_anchor": plan_input.tone_anchor,
            "target_word_count": plan_input.target_word_count,
        },
        ensure_ascii=False,
    )

    run, result, meta = await call_agent(
        book_id=plan_input.book_id,
        agent_role="chapter_planner",
        user_content=user_content,
        chapter_id=plan_input.chapter_id,
    )

    plan = _coerce_plan(result, meta)
    if plan and plan.get("scenes"):
        return plan

    logger.warning(
        f"ChapterPlanner LLM plan unusable, using deterministic fallback. meta={meta}"
    )
    return _deterministic_plan(plan_input.outline, plan_input.target_word_count)


async def plan_chapter(
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    outline_node_id: uuid.UUID | None = None,
    outline_node: OutlineNode | None = None,
    forced_dependencies: list[dict] | None = None,
    l4_states: dict | None = None,
    retrieved_evidence: list | None = None,
    target_word_count: int = 3000,
    **_deprecated,
) -> dict | None:
    """Backward-compatible entry. Prefer load + generate."""
    oid = outline_node_id or (outline_node.id if outline_node is not None else None)
    if oid is None:
        raise ValueError("outline_node_id required")
    plan_input = await load_chapter_plan_input(
        book_id=book_id,
        chapter_id=chapter_id,
        outline_node_id=oid,
        forced_dependencies=forced_dependencies or [],
        l4_states=l4_states or {},
        retrieved_evidence=retrieved_evidence,
        target_word_count=target_word_count,
    )
    return await generate_chapter_plan(plan_input)
