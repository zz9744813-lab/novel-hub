"""ReviewAgent / ContinuityJudge - checks chapter for issues.
Per §7.2 Step 6+8 + §A.4 v7.3.

P0: fail-closed on agent failure (do NOT auto-pass).
"""
from __future__ import annotations

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

    Also serves as ContinuityJudge (§7.2 Step 8).
    Fail-closed: agent failure => (False, [service_error issue]).
    """
    # Get L4 state for involved characters
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

    # Get voice cards
    vc = await db.execute(select(StyleVoiceCard).where(StyleVoiceCard.book_id == book_id))
    voice_cards = [
        {"register": v.register, "emotion_expression": v.emotion_expression}
        for v in vc.scalars().all()
    ]

    # Get tone anchor
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
        logger.error(f"ReviewAgent failed: {meta}")
        # P0 fail-closed: service failure is NOT a pass
        return False, [{
            "issue_id": "review_service_failure",
            "issue_cluster_id": "service",
            "severity": "critical",
            "category": "service_error",
            "message": meta.get("block_reason") or meta.get("error") or "ReviewAgent failed",
        }]

    if not isinstance(result, dict):
        return False, [{
            "issue_id": "review_invalid_payload",
            "issue_cluster_id": "service",
            "severity": "critical",
            "category": "service_error",
            "message": "ReviewAgent returned non-dict payload",
        }]

    passed = bool(result.get("passed", False))
    issues = result.get("issues", []) or []
    return passed, issues
