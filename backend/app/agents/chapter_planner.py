"""ChapterPlannerAgent - expands outline node into beat sheet + scene plan.
Per §7.2 Step 4 + §A.2 v7.3.
"""
import uuid
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.agents.caller import call_agent
from app.models import OutlineNode, MemoryL4StateSnapshot, MemoryL2StageSummary, MemoryL3VolumeSummary, StyleVoiceCard, StyleToneAnchor, Scene

logger = logging.getLogger("novelforge.chapter_planner")


async def plan_chapter(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    outline_node: OutlineNode,
    forced_dependencies: list[dict],
    l4_states: dict,
    target_word_count: int = 3000,
) -> dict | None:
    """Generate chapter beat sheet and scene plan.
    
    Returns the scene plan dict or None on failure.
    """
    # Get L2/L3 summaries
    l2 = await db.execute(
        select(MemoryL2StageSummary).where(MemoryL2StageSummary.book_id == book_id)
        .order_by(MemoryL2StageSummary.chapter_range_end.desc()).limit(1)
    )
    l2_summary = l2.scalar_one_or_none()

    l3 = await db.execute(
        select(MemoryL3VolumeSummary).where(MemoryL3VolumeSummary.book_id == book_id)
        .order_by(MemoryL3VolumeSummary.volume_no.desc()).limit(1)
    )
    l3_summary = l3.scalar_one_or_none()

    # Get voice cards
    vc = await db.execute(
        select(StyleVoiceCard).where(StyleVoiceCard.book_id == book_id)
    )
    voice_cards = [{"character_id": str(v.character_id), "register": v.register,
                     "emotion_expression": v.emotion_expression} for v in vc.scalars().all()]

    # Get tone anchor
    ta = await db.execute(
        select(StyleToneAnchor).where(StyleToneAnchor.book_id == book_id)
        .order_by(StyleToneAnchor.version.desc()).limit(1)
    )
    tone_anchor = ta.scalar_one_or_none()
    tone_dict = {"narrative_pov": tone_anchor.narrative_pov,
                  "emotional_temperature": tone_anchor.emotional_temperature,
                  "pacing": tone_anchor.pacing} if tone_anchor else {}

    user_content = json.dumps({
        "chapter_outline_node": {
            "chapter_no": outline_node.chapter_no,
            "title": outline_node.title,
            "goal": outline_node.goal,
            "required_beats": outline_node.required_beats,
            "forbidden_outcomes": outline_node.forbidden_outcomes,
            "depends_on": outline_node.depends_on,
            "expected_state_changes": outline_node.expected_state_changes,
        },
        "forced_dependencies": forced_dependencies,
        "l4_state": l4_states,
        "l2_summary": l2_summary.summary_json if l2_summary else {},
        "l3_summary": l3_summary.summary_json if l3_summary else {},
        "event_and_retrieved_evidence": [],  # Filled by retrieval engine
        "voice_cards": voice_cards,
        "tone_anchor": tone_dict,
        "target_word_count": target_word_count,
    }, ensure_ascii=False)

    run, result, meta = await call_agent(
        db=db,
        book_id=book_id,
        agent_role="chapter_planner",
        user_content=user_content,
        chapter_id=chapter_id,
    )

    if not result or "scenes" not in result:
        logger.error(f"ChapterPlanner failed: {meta}")
        return None

    return result
