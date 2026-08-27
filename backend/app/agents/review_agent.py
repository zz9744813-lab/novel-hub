"""ReviewAgent / ContinuityJudge - checks chapter for issues.
Per §7.2 Step 6+8 + §A.4 v7.3.

P0: fail-closed on agent failure.
P0-03: load DTOs in short session; LLM without session.
"""
from __future__ import annotations

import uuid
import json
import logging
from sqlalchemy import select
from app.database import async_session_factory
from app.agents.caller import call_agent
from app.models import (
    CharacterCoreAnchor,
    OutlineNode,
    MemoryL4StateSnapshot,
    StyleVoiceCard,
    StyleToneAnchor,
)

logger = logging.getLogger("novelforge.review")


async def review_chapter(
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    chapter_content: str,
    outline_node_id: uuid.UUID | None = None,
    outline_data: dict | None = None,
    scene_contracts: list[dict] | None = None,
    **_deprecated,
) -> tuple[bool, list[dict]]:
    """Review chapter for issues. Returns (passed, issues)."""

    async with async_session_factory() as db:
        if outline_data is None:
            if outline_node_id is None:
                raise ValueError("outline_node_id or outline_data required")
            node = (
                await db.execute(select(OutlineNode).where(OutlineNode.id == outline_node_id))
            ).scalar_one_or_none()
            if not node:
                return False, [{
                    "issue_id": "outline_missing",
                    "issue_cluster_id": "service",
                    "severity": "critical",
                    "category": "service_error",
                    "message": "outline node missing",
                }]
            outline_data = {
                "chapter_no": node.chapter_no,
                "goal": node.goal,
                "required_beats": node.required_beats,
                "forbidden_outcomes": node.forbidden_outcomes,
                "depends_on": node.depends_on,
                "involved_character_ids": node.involved_character_ids or [],
            }

        l4_states = {}
        for char_id in outline_data.get("involved_character_ids", [])[:20]:
            cid = uuid.UUID(char_id) if isinstance(char_id, str) else char_id
            snap = await db.execute(
                select(MemoryL4StateSnapshot)
                .where(
                    MemoryL4StateSnapshot.book_id == book_id,
                    MemoryL4StateSnapshot.entity_id == cid,
                )
                .order_by(MemoryL4StateSnapshot.as_of_chapter.desc())
                .limit(1)
            )
            s = snap.scalar_one_or_none()
            if s:
                l4_states[str(char_id)] = s.state

        vc = await db.execute(select(StyleVoiceCard).where(StyleVoiceCard.book_id == book_id))
        voice_cards = [
            {"register": v.register, "emotion_expression": v.emotion_expression}
            for v in vc.scalars().all()
        ]

        ta = await db.execute(
            select(StyleToneAnchor)
            .where(StyleToneAnchor.book_id == book_id)
            .order_by(StyleToneAnchor.version.desc())
            .limit(1)
        )
        tone = ta.scalar_one_or_none()
        tone_dict = {"narrative_pov": tone.narrative_pov} if tone else {}

        # v9: Core Anchors for cognitive-causal review (spec §28)
        try:
            anchor_rows = (
                await db.execute(
                    select(CharacterCoreAnchor).where(
                        CharacterCoreAnchor.book_id == book_id,
                        CharacterCoreAnchor.status == "active",
                    )
                )
            ).scalars().all()
            anchor_rows.sort(key=lambda r: (not r.is_locked, -(r.priority or 0.5)))
            core_anchors = [
                {
                    "character_id": str(r.character_id),
                    "anchor_code": r.anchor_code,
                    "anchor_type": r.anchor_type,
                    "statement": r.statement,
                    "is_locked": r.is_locked,
                }
                for r in anchor_rows[:40]
            ]
        except Exception as e:
            logger.debug("core anchors skip in review: %s", e)
            core_anchors = []

    user_content = json.dumps(
        {
            "chapter_content": chapter_content,
            "l4_state": l4_states,
            "voice_cards": voice_cards,
            "tone_anchor": tone_dict,
            "outline_node": {
                "chapter_no": outline_data.get("chapter_no"),
                "goal": outline_data.get("goal"),
                "required_beats": outline_data.get("required_beats"),
                "forbidden_outcomes": outline_data.get("forbidden_outcomes"),
                "depends_on": outline_data.get("depends_on"),
                "target_char_range": outline_data.get("target_char_range"),
            },
            "depends_on": outline_data.get("depends_on"),
            "scene_contracts": scene_contracts or [],
            "core_anchors": core_anchors,
        },
        ensure_ascii=False,
    )

    run, result, meta = await call_agent(
        book_id=book_id,
        agent_role="review_agent",
        user_content=user_content,
        chapter_id=chapter_id,
    )

    if not result:
        logger.error(f"ReviewAgent failed: {meta}")
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

    return bool(result.get("passed", False)), result.get("issues", []) or []
