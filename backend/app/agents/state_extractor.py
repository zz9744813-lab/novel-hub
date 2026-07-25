"""StateExtractorAgent + StateCommitter - extracts events and commits L4 atomically.
Per §7.2 Step 9-10 + §5.5 v7.3.

P0 fixes:
- commit() after flush so L4/L1/events/search docs are not rolled back
- content_hash uses SHA-256 (not Python hash())
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


def _sha256_int_or_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def extract_and_commit(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    chapter_no: int,
    chapter_content: str,
    scenes: list[dict],
    outline_node: OutlineNode,
    current_l4: dict,
    source_run_id: uuid.UUID,
) -> tuple[bool, list[str]]:
    """Extract state events from finalized content and commit atomically.

    Per §5.5: story_events -> L4 -> L1 -> search_documents -> commit
    All in one transaction. Any failure = rollback.
    """
    user_content = json.dumps({
        "chapter_content": chapter_content,
        "scenes": scenes,
        "paragraphs": [],
        "current_l4": current_l4,
        "outline_node": {
            "chapter_no": outline_node.chapter_no,
            "goal": outline_node.goal,
            "expected_state_changes": outline_node.expected_state_changes,
        },
    }, ensure_ascii=False)

    run, result, meta = await call_agent(
        db=db,
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

    # §5.5: Filter to explicit-only events
    explicit_events = [e for e in events if e.get("certainty") == "explicit"]

    # Atomic commit: story_events -> L4 (merged) -> L1
    await commit_l4_with_events(
        db=db,
        book_id=book_id,
        chapter_id=chapter_id,
        as_of_chapter=chapter_no,
        events=explicit_events,
        source_run_id=source_run_id,
    )

    # Generate scene_search_documents with proper tsvector
    from app.models import SceneSearchDocument
    for scene in scenes:
        scene_id = scene.get("scene_id")
        if isinstance(scene_id, str):
            try:
                scene_id = uuid.UUID(scene_id)
            except ValueError:
                scene_id = uuid.uuid4()
        elif scene_id is None:
            scene_id = uuid.uuid4()

        scene_content = scene.get("content", chapter_content[:1000])
        scene_summary = scene.get("summary", scene_content[:200])

        search_doc = SceneSearchDocument(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=chapter_id,
            scene_id=scene_id,
            chapter_no=chapter_no,
            scene_no=scene.get("scene_no", 1),
            outline_node_id=outline_node.id,
            pov_character_id=None,
            character_ids=outline_node.involved_character_ids,
            location_ids=[],
            item_ids=[],
            plot_thread_ids=outline_node.plot_thread_ids,
            event_types=[e.get("entity_type") for e in explicit_events],
            scene_summary=scene_summary,
            evidence_excerpt=chapter_content[:500],
            search_text=scene_content,
            search_tsv="",  # Set via SQL below
            canon_status="canon",
            content_hash=_sha256_int_or_hex(scene_content),
            version=1,
        )
        db.add(search_doc)
        await db.flush()

        # Basic tsvector; Chinese pre-tokenizer still TODO (P1)
        await db.execute(
            text("""
                UPDATE scene_search_documents
                SET search_tsv = to_tsvector('simple', :search_text)
                WHERE id = :doc_id
            """),
            {"search_text": search_doc.search_text, "doc_id": str(search_doc.id)},
        )

    # CRITICAL P0: commit so L4/L1/events/search docs survive session close
    await db.commit()
    return True, conflicts
