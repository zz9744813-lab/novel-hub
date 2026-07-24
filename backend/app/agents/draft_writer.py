"""DraftWriterAgent - writes one scene at a time, streaming.
Per §7.2 Step 5 + §A.3 v7.3.
"""
import uuid
import json
import logging
import hashlib
from sqlalchemy.ext.asyncio import AsyncSession
from app.agents.caller import call_agent
from app.models import Scene

logger = logging.getLogger("novelforge.draft_writer")


async def write_scene(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    scene_plan: dict,
    context_package: dict,
    previous_scene_tail: str = "",
    target_word_count: int = 2000,
) -> tuple[str | None, str | None]:
    """Write a single scene.
    
    Returns (content, error_reason).
    """
    user_content = json.dumps({
        "scene_plan": scene_plan,
        "context_package": context_package,
        "previous_scene_tail": previous_scene_tail,
        "target_word_count": target_word_count,
    }, ensure_ascii=False)

    run, result, meta = await call_agent(
        db=db,
        book_id=book_id,
        agent_role="draft_writer",
        user_content=user_content,
        chapter_id=chapter_id,
        scene_id=None,  # Scene not created yet
    )

    if result is None:
        return None, meta.get("block_reason", "unknown")

    # Check for PIPELINE_BLOCKED
    if isinstance(result, str) and result.startswith("PIPELINE_BLOCKED"):
        return None, result

    return result, None
