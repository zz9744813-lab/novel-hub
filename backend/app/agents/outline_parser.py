"""OutlineParserAgent - parses raw outline into structured DAG."""
import uuid
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.gateway.model_gateway import stream_completion_and_collect
from app.gateway.normalizer import normalize_json
from app.models import OutlineVersion, OutlineNode, OutlineDependency, CharacterCard, WorldRule, PlotThread

logger = logging.getLogger("novelforge.outline_parser")


async def parse_outline(
    db: AsyncSession,
    book_id: uuid.UUID,
    outline_version_id: uuid.UUID,
    raw_outline: str,
    target_chapter_count: int = 500,
) -> tuple[bool, list[str]]:
    """Parse raw outline text into structured nodes + dependencies."""
    # Get known entities (quick queries, no LLM)
    characters = await db.execute(select(CharacterCard).where(CharacterCard.book_id == book_id))
    char_list = [{"id": str(c.id), "name": c.name} for c in characters.scalars().all()]

    world_rules = await db.execute(select(WorldRule).where(WorldRule.book_id == book_id))
    rule_list = [{"rule_key": r.rule_key, "description": r.description} for r in world_rules.scalars().all()]

    threads = await db.execute(select(PlotThread).where(PlotThread.book_id == book_id))
    thread_list = [{"id": str(t.id), "name": t.name, "status": t.status} for t in threads.scalars().all()]

    # Build LLM input
    from app.prompts import PROMPTS
    prompt_config = PROMPTS["outline_parser"]
    user_content = json.dumps({
        "book_id": str(book_id),
        "outline_version": 1,
        "raw_outline": raw_outline,
        "known_characters": char_list,
        "known_world_rules": rule_list,
        "known_plot_threads": thread_list,
        "target_chapter_count": target_chapter_count,
    }, ensure_ascii=False)

    # Call LLM (this takes time - DB session may expire)
    result = await stream_completion_and_collect(
        system_prompt=prompt_config["system_prompt"],
        user_content=user_content,
        model="deepseek-v4-flash",
        temperature=0.1,
    )

    parsed = normalize_json(result.final_content) if result.final_content else None

    if not parsed or "nodes" not in parsed:
        return False, [f"Agent failed: {result.error or 'no output'}"]

    # Store parsed nodes
    for node_data in parsed.get("nodes", []):
        try:
            node_id = uuid.UUID(node_data["node_id"]) if isinstance(node_data.get("node_id"), str) and len(node_data.get("node_id", "")) > 10 else uuid.uuid4()
        except (ValueError, KeyError):
            node_id = uuid.uuid4()
        
        node = OutlineNode(
            id=node_id,
            book_id=book_id,
            outline_version_id=outline_version_id,
            node_type=node_data.get("node_type", "chapter"),
            volume_no=node_data.get("volume_no", 1),
            chapter_no=node_data.get("chapter_no", 0),
            title=node_data.get("title"),
            goal=node_data.get("goal", ""),
            required_beats=node_data.get("required_beats", []),
            forbidden_outcomes=node_data.get("forbidden_outcomes", []),
            involved_character_ids=node_data.get("involved_character_ids", []),
            plot_thread_ids=node_data.get("plot_thread_ids", []),
            depends_on=node_data.get("depends_on", []),
            expected_state_changes=node_data.get("expected_state_changes", []),
        )
        db.add(node)

        for dep in node_data.get("depends_on", []):
            if dep.get("required"):
                try:
                    dep_record = OutlineDependency(
                        id=uuid.uuid4(),
                        book_id=book_id,
                        outline_version_id=outline_version_id,
                        source_node_id=node.id,
                        target_node_id=uuid.UUID(dep["node_id"]) if isinstance(dep.get("node_id"), str) and len(dep.get("node_id", "")) > 10 else uuid.uuid4(),
                        dependency_type=dep.get("dependency_type", "plot_thread"),
                        required=True,
                        required_state=dep.get("required_state"),
                    )
                    db.add(dep_record)
                except (ValueError, KeyError):
                    pass

    await db.flush()

    # Mark version as parsed
    ov = await db.execute(select(OutlineVersion).where(OutlineVersion.id == outline_version_id))
    outline_ver = ov.scalar_one_or_none()
    if outline_ver:
        outline_ver.parsed_json = parsed
        outline_ver.status = "parsed"

    await db.flush()
    return True, []
