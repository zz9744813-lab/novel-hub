"""StateExtractorAgent + StateCommitter - extracts events and commits L4 atomically.
Per §7.2 Step 9-10 + §5.5 v7.3.

P0-03: LLM phase without holding caller's session across await.
P0: commit() after flush; SHA-256 content_hash.
"""
from __future__ import annotations

import uuid
import json
import hashlib
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.agents.caller import call_agent
from app.engine.memory import commit_l4_with_events
from app.models import OutlineNode

logger = logging.getLogger("novelforge.state_extractor")


def _sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def extract_and_commit(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    chapter_no: int,
    chapter_content: str,
    scenes: list[dict],
    outline_node: OutlineNode | dict,
    current_l4: dict,
    source_run_id: uuid.UUID,
) -> tuple[bool, list[str]]:
    """Extract state events then commit in the *write* session.

    LLM call is session-free (call_agent). The provided `db` is only used for
    the final write phase after LLM returns.
    """
    if isinstance(outline_node, OutlineNode):
        outline_payload = {
            "chapter_no": outline_node.chapter_no,
            "goal": outline_node.goal,
            "expected_state_changes": outline_node.expected_state_changes,
        }
    else:
        outline_payload = {
            "chapter_no": outline_node.get("chapter_no"),
            "goal": outline_node.get("goal"),
            "expected_state_changes": outline_node.get("expected_state_changes"),
        }

    user_content = json.dumps(
        {
            "chapter_content": chapter_content,
            "scenes": scenes,
            "paragraphs": [],
            "current_l4": current_l4,
            "outline_node": outline_payload,
        },
        ensure_ascii=False,
    )

    # LLM Phase — no session held by call_agent
    run, result, meta = await call_agent(
        book_id=book_id,
        agent_role="state_extractor",
        user_content=user_content,
        chapter_id=chapter_id,
    )

    if not result:
        logger.error(f"StateExtractor failed: {meta}")
        return False, [meta.get("block_reason", "extraction failed")]

    events = result.get("events", []) if isinstance(result, dict) else []
    conflicts = result.get("conflicts", []) if isinstance(result, dict) else []

    if conflicts:
        logger.warning(f"StateExtractor found {len(conflicts)} conflicts with L4")

    explicit_events = [e for e in events if e.get("certainty") == "explicit"]

    await commit_l4_with_events(
        db=db,
        book_id=book_id,
        chapter_id=chapter_id,
        as_of_chapter=chapter_no,
        events=explicit_events,
        source_run_id=source_run_id or run.id,
    )

    # Search documents for scenes (SHA-256)
    from app.engine.chinese_tokenizer import tokenize_for_search
    from app.models.tables import SceneSearchDocument

    for sc in scenes:
        content = sc.get("content") or ""
        content_hash = _sha256_hex(content)
        search_text = content[:8000]
        tokenized = tokenize_for_search(search_text)
        doc = SceneSearchDocument(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=chapter_id,
            scene_id=sc.get("scene_id") or uuid.uuid4(),
            chapter_no=chapter_no,
            scene_no=sc.get("scene_no") or 0,
            search_text=search_text,
            content_hash=content_hash,
        )
        db.add(doc)
        await db.flush()
        await db.execute(
            text(
                """
                UPDATE scene_search_documents
                SET search_tsv = to_tsvector('simple', :tok)
                WHERE id = :id
                """
            ),
            {"tok": tokenized or search_text, "id": doc.id},
        )

    await db.commit()
    return True, []
