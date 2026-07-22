"""ReviewAgent / ContinuityJudge - checks chapter for issues.
Per §7.2 Step 6+8 + §A.4 v7.3.
FIX: Fail-Closed — agent failure does NOT mean pass.
"""
import uuid
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.agents.caller import call_agent
from app.models import OutlineNode, MemoryL4StateSnapshot, StyleVoiceCard, StyleToneAnchor

logger = logging.getLogger("novelforge.review")


async def review_chapter(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    chapter_content: str,
    outline_node: OutlineNode,
) -> tuple[bool, list[dict]]:
    """Review chapter for issues. Returns (passed, issues).
    
    FIX: Fail-Closed — if agent fails, chapter does NOT pass review.
    The caller should handle failed review by retrying or escalating.
    """
    l4_states = {}
    for char_id in outline_node.involved_character_ids:
        cid = uuid.UUID(char_id) if isinstance(char_id, str) else char_id
        snap = await db.execute(
            select(MemoryL4StateSnapshot).where(
                MemoryL4StateSnapshot.book_id == book_id,
                MemoryL4StateSnapshot.entity_id == cid,
            ).order_by(MemoryL4StateSnapshot.as_of_chapter.desc()).limit(1)
        )
        s = snap.scalar_one_or_none()
        if s:
            l4_states[str(char_id)] = s.state

    vc = await db.execute(select(StyleVoiceCard).where(StyleVoiceCard.book_id == book_id))
    voice_cards = [{"register": v.register, "emotion_expression": v.emotion_expression} for v in vc.scalars().all()]
    ta = await db.execute(
        select(StyleToneAnchor).where(StyleToneAnchor.book_id == book_id)
        .order_by(StyleToneAnchor.version.desc()).limit(1)
    )
    tone = ta.scalar_one_or_none()

    user_content = json.dumps({
        "chapter_content": chapter_content,
        "l4_state": l4_states,
        "voice_cards": voice_cards,
        "tone_anchor": {"narrative_pov": tone.narrative_pov} if tone else {},
        "outline_node": {
            "chapter_no": outline_node.chapter_no,
            "goal": outline_node.goal,
            "required_beats": outline_node.required_beats,
            "forbidden_outcomes": outline_node.forbidden_outcomes,
            "depends_on": outline_node.depends_on,
        },
        "depends_on": outline_node.depends_on,
    }, ensure_ascii=False)

    run, result, meta = await call_agent(
        db=db,
        book_id=book_id,
        agent_role="review_agent",
        user_content=user_content,
        chapter_id=chapter_id,
    )

    if not result:
        logger.error(f"ReviewAgent failed (FAIL-CLOSED): {meta}")
        # FAIL-CLOSED: agent failure means review did NOT pass.
        # Return (False, []) to signal failure without issues to patch.
        return False, []  # caller should treat this as NEEDS_HUMAN or retry

    passed = result.get("passed", False)
    issues = result.get("issues", [])
    return passed, issues
