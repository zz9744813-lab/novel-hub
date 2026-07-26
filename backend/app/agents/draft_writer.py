"""DraftWriterAgent - writes one scene at a time, streaming.
Per §7.2 Step 5 + §A.3 v7.3.

P0-03: no DB session during LLM.
"""
from __future__ import annotations

import uuid
import json
import logging
from app.agents.caller import call_agent

logger = logging.getLogger("novelforge.draft_writer")


async def write_scene(
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    scene_plan: dict,
    context_package: dict,
    previous_scene_tail: str = "",
    target_word_count: int = 2000,
    **_deprecated,
) -> tuple[str | None, str | None]:
    """Write a single scene. Returns (content, error_reason)."""
    user_content = json.dumps(
        {
            "scene_plan": scene_plan,
            "context_package": context_package,
            "previous_scene_tail": previous_scene_tail,
            "target_word_count": target_word_count,
        },
        ensure_ascii=False,
    )

    run, result, meta = await call_agent(
        book_id=book_id,
        agent_role="draft_writer",
        user_content=user_content,
        chapter_id=chapter_id,
        scene_id=None,
    )

    if result is None:
        return None, meta.get("block_reason", "unknown")

    if isinstance(result, str) and result.startswith("PIPELINE_BLOCKED"):
        return None, result

    return result, None
