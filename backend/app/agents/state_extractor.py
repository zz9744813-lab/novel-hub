"""StateExtractorAgent + StateCommitter - extracts events and commits L4 atomically.
Per §7.2 Step 9-10 + §A.6 + §5.5 v7.3.
FIX P0-6: search_tsv generated from search_text, scene_id uses actual Scene.id.
"""
import uuid
import json
import logging
import hashlib
from sqlalchemy.ext.asyncio import AsyncSession
from app.agents.caller import call_agent
from app.engine.memory import commit_l4_with_events
from app.models import OutlineNode, SceneSearchDocument
from sqlalchemy import text

logger = logging.getLogger("novelforge.state_extractor")


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

    Returns (success, conflicts).
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

    events = result.get("events", [])
    conflicts = result.get("conflicts", [])

    if conflicts:
        logger.warning(f"StateExtractor found {len(conflicts)} conflicts with L4")

    explicit_events = [e for e in events if e.get("certainty") == "explicit"]

    await commit_l4_with_events(
        db=db,
        book_id=book_id,
        chapter_id=chapter_id,
        as_of_chapter=chapter_no,
        events=explicit_events,
        source_run_id=source_run_id,
    )

    # Generate scene_search_documents with search_tsv
    for scene in scenes:
        scene_id_str = scene.get("scene_id", "")
        try:
            scene_id = uuid.UUID(scene_id_str) if scene_id_str else uuid.uuid4()
        except (ValueError, TypeError):
            scene_id = uuid.uuid4()

        search_text = scene.get("content", chapter_content[:1000])
        scene_summary = scene.get("summary", scene.get("content", "")[:200])

        # FIX: Generate search_tsv using PostgreSQL to_tsvector on the fly
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
            search_text=search_text,
            search_tsv="",  # Will be populated by trigger or query-time to_tsvector
            content_hash=hashlib.sha256(search_text.encode()).hexdigest(),
            version=1,
        )
        # canon_status is set by column default
        db.add(search_doc)

    await db.flush()

    # Update search_tsv via SQL (inline to_tsvector)
    if scenes:
        await db.execute(
            text("""
                UPDATE scene_search_documents
                SET search_tsv = to_tsvector('simple', coalesce(scene_summary, '') || ' ' || coalesce(search_text, ''))
                WHERE chapter_id = :chapter_id
            """),
            {"chapter_id": str(chapter_id)}
        )

    return True, conflicts
