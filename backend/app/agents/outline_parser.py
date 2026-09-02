
"""OutlineParserAgent - parses raw outline into structured DAG."""
import uuid
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.gateway.model_gateway import stream_completion_and_collect
from app.gateway.normalizer import normalize_json
from app.models import OutlineVersion, OutlineNode, CharacterCard, WorldRule, PlotThread

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
    from app.prompts import AGENT_MODELS, PROMPTS
    prompt_config = PROMPTS["outline_parser"]
    model = AGENT_MODELS["outline_parser"]
    user_content = json.dumps({
        "book_id": str(book_id),
        "outline_version": 1,
        "raw_outline": raw_outline,
        "known_characters": char_list,
        "known_world_rules": rule_list,
        "known_plot_threads": thread_list,
        "target_chapter_count": target_chapter_count,
    }, ensure_ascii=False)

    # Call LLM with extra logging
    logger.warning(f"OUTLINE_PARSER: calling LLM model={model}, user_content_len={len(user_content)}")
    print(f"OUTLINE_PARSER: calling LLM model={model}, user_content_len={len(user_content)}", flush=True)

    result = await stream_completion_and_collect(
        system_prompt=prompt_config["system_prompt"],
        user_content=user_content,
        model=model,
        temperature=0.1,
        max_tokens=16384,
        reasoning_mode="disabled",
    )

    logger.warning(f"OUTLINE_PARSER: result error={result.error}, final_content_len={len(result.final_content)}, reasoning_len={len(result.reasoning_text)}, latency={result.latency_ms}ms")
    print(f"OUTLINE_PARSER: result error={result.error}, final_content_len={len(result.final_content)}, reasoning_len={len(result.reasoning_text)}, latency={result.latency_ms}ms", flush=True)

    if result.final_content:
        logger.warning(f"OUTLINE_PARSER: final_content preview: {result.final_content[:200]}")
        print(f"OUTLINE_PARSER: final_content preview: {result.final_content[:200]}", flush=True)

    parsed = normalize_json(result.final_content) if result.final_content else None

    if not parsed or "nodes" not in parsed:
        return False, [f"Agent failed: {result.error or 'no output'}"]

    # Store parsed nodes - first pass: create all nodes, build chapter_no -> UUID map
    chapter_to_uuid = {}
    raw_nodes = parsed.get("nodes", [])

    for node_data in raw_nodes:
        try:
            node_id = uuid.UUID(node_data["node_id"]) if isinstance(node_data.get("node_id"), str) and len(node_data.get("node_id", "")) > 10 else uuid.uuid4()
        except (ValueError, KeyError):
            node_id = uuid.uuid4()

        ch_no = node_data.get("chapter_no", 0)
        chapter_to_uuid[ch_no] = str(node_id)

        node = OutlineNode(
            id=node_id,
            book_id=book_id,
            outline_version_id=outline_version_id,
            node_type=node_data.get("node_type", "chapter"),
            volume_no=node_data.get("volume_no", 1),
            chapter_no=ch_no,
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

    await db.flush()

    # Second pass: resolve chapter-number references in depends_on to actual UUIDs
    for node_data in raw_nodes:
        ch_no = node_data.get("chapter_no", 0)
        deps = node_data.get("depends_on", [])
        if not deps:
            continue

        resolved_deps = []
        for dep in deps:
            dep_copy = dict(dep)
            dep_id_raw = dep_copy.get("node_id", "")

            if isinstance(dep_id_raw, str) and len(dep_id_raw) > 10:
                try:
                    uuid.UUID(dep_id_raw)
                    resolved_deps.append(dep_copy)
                    continue
                except ValueError:
                    pass

            dep_ch_no = None
            if isinstance(dep_id_raw, int):
                dep_ch_no = dep_id_raw
            elif isinstance(dep_id_raw, str):
                cleaned = dep_id_raw.lower().replace("ch_", "").replace("ch", "").replace("chapter_", "").replace("chapter", "").strip()
                try:
                    dep_ch_no = int(cleaned)
                except ValueError:
                    pass

            if dep_ch_no is not None and dep_ch_no in chapter_to_uuid:
                dep_copy["node_id"] = chapter_to_uuid[dep_ch_no]
                resolved_deps.append(dep_copy)
            else:
                resolved_deps.append(dep_copy)

        node_uuid = chapter_to_uuid.get(ch_no)
        if node_uuid:
            await db.execute(
                update(OutlineNode.__table__)
                .where(OutlineNode.id == uuid.UUID(node_uuid))
                .values(depends_on=resolved_deps)
            )

    await db.flush()

    # Mark version as parsed
    ov = await db.execute(select(OutlineVersion).where(OutlineVersion.id == outline_version_id))
    outline_ver = ov.scalar_one_or_none()
    if outline_ver:
        outline_ver.parsed_json = parsed
        outline_ver.status = "parsed"

    await db.flush()
    return True, []
