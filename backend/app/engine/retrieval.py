"""SQL-first retrieval engine - §6 v7.3.
9-step fixed retrieval chain with deterministic fallback.
"""
import uuid
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    OutlineNode, OutlineDependency, MemoryL4StateSnapshot,
    PlotThread, StoryEvent, SceneSearchDocument, EntityAlias,
    QueryPlan, RetrievalRun, RetrievalCandidate,
)
from app.gateway.model_gateway import stream_completion_and_collect
from app.prompts import PROMPTS, AGENT_MODELS, AGENT_TEMPERATURES
import json
import time


# Rule scores per §6.6
SCORE_WEIGHTS = {
    "required_dependency": 1000,
    "human_locked": 900,
    "open_plot_thread": 700,
    "character_overlap": 180,
    "item_overlap": 180,
    "location_overlap": 120,
    "event_type_match": 120,
    "full_text_max": 100,
    "same_outline_arc": 40,
    "recency_max": 20,
}


async def dependency_resolver(db: AsyncSession, book_id: uuid.UUID, outline_node_id: uuid.UUID) -> list[dict]:
    """Step 1: Read required dependencies directly."""
    result = await db.execute(
        select(OutlineDependency).where(
            OutlineDependency.book_id == book_id,
            OutlineDependency.source_node_id == outline_node_id,
            OutlineDependency.required == True,
        )
    )
    deps = result.scalars().all()
    return [{"dep_id": str(d.id), "target_node_id": str(d.target_node_id),
             "dependency_type": d.dependency_type, "required_state": d.required_state} for d in deps]


async def state_resolver(db: AsyncSession, book_id: uuid.UUID, character_ids: list[uuid.UUID], as_of_chapter: int) -> dict:
    """Step 2: Read L4 current state for involved entities."""
    states = {}
    for cid in character_ids:
        result = await db.execute(
            select(MemoryL4StateSnapshot).where(
                MemoryL4StateSnapshot.book_id == book_id,
                MemoryL4StateSnapshot.entity_id == cid,
                MemoryL4StateSnapshot.as_of_chapter <= as_of_chapter,
            ).order_by(MemoryL4StateSnapshot.as_of_chapter.desc(), MemoryL4StateSnapshot.version.desc()).limit(1)
        )
        snap = result.scalar_one_or_none()
        if snap:
            states[str(cid)] = {"state": snap.state, "is_locked": snap.is_locked}
    return states


async def plot_thread_resolver(db: AsyncSession, book_id: uuid.UUID, thread_ids: list[uuid.UUID]) -> list[dict]:
    """Step 3: Read open plot threads."""
    result = await db.execute(
        select(PlotThread).where(
            PlotThread.book_id == book_id,
            PlotThread.status == "open",
        )
    )
    threads = result.scalars().all()
    return [{"thread_id": str(t.id), "name": t.name, "planted_chapter": t.planted_chapter} for t in threads]


async def event_ledger_search(db: AsyncSession, book_id: uuid.UUID,
                               character_ids: list, event_types: list,
                               chapter_range: tuple[int, int]) -> list[dict]:
    """Step 4: Query story_events by entity, event type, chapter range."""
    # Simplified: query by book_id and chapter range
    result = await db.execute(
        select(StoryEvent).where(
            StoryEvent.book_id == book_id,
        ).order_by(StoryEvent.created_at.desc()).limit(60)
    )
    events = result.scalars().all()
    # TODO: proper filtering by character_ids, event_types, chapter_range
    return [{"event_id": str(e.id), "event_type": e.event_type,
             "certainty": e.certainty, "evidence_excerpt": e.evidence_excerpt[:200]} for e in events]


async def full_text_search(db: AsyncSession, book_id: uuid.UUID,
                            search_terms: list[str], chapter_range: tuple[int, int]) -> list[dict]:
    """Step 5: tsvector full-text search on scene_search_documents."""
    if not search_terms:
        return []
    query = " && ".join(search_terms)
    # Use to_tsquery with simple config
    result = await db.execute(
        text("""
            SELECT id, chapter_no, scene_no, scene_summary, ts_rank(search_tsv, plainto_tsquery('simple', :q)) as rank
            FROM scene_search_documents
            WHERE book_id = :book_id AND canon_status = 'canon'
            ORDER BY rank DESC
            LIMIT 40
        """),
        {"q": query, "book_id": str(book_id)}
    )
    rows = result.fetchall()
    return [{"id": str(r[0]), "chapter_no": r[1], "scene_no": r[2],
             "scene_summary": r[3], "rank": r[4]} for r in rows]


def candidate_merge_and_score(event_candidates: list, ft_candidates: list,
                               query_plan: dict) -> list[dict]:
    """Step 6: Merge, deduplicate by scene_id, score with rules per §6.6."""
    seen = set()
    scored = []

    for c in event_candidates:
        key = c.get("event_id", c.get("id", ""))
        if key in seen:
            continue
        seen.add(key)
        score = 0
        # character overlap
        # TODO: full scoring with query_plan character_ids
        scored.append({**c, "rule_score": score, "source_type": "story_event"})

    for c in ft_candidates:
        key = c.get("id", "")
        if key in seen:
            continue
        seen.add(key)
        score = int(c.get("rank", 0) * 100)  # full_text_rank 0-100
        scored.append({**c, "rule_score": score, "source_type": "scene"})

    scored.sort(key=lambda x: x.get("rule_score", 0), reverse=True)
    return scored[:24]


async def evidence_ranker_agent(candidates: list, semantic_questions: list,
                                  chapter_goal: str) -> list[dict]:
    """Step 7: Use LLM to rank Top 24 candidates, return Top 8."""
    if not candidates:
        return []
    prompt = PROMPTS["evidence_ranker"]["system_prompt"]
    user_content = json.dumps({
        "candidates": candidates[:24],
        "semantic_questions": semantic_questions,
        "chapter_goal": chapter_goal,
    }, ensure_ascii=False)

    result = await stream_completion_and_collect(
        system_prompt=prompt,
        user_content=user_content,
        model=AGENT_MODELS["evidence_ranker"],
        temperature=AGENT_TEMPERATURES["evidence_ranker"],
    )

    if result.error or not result.final_content:
        # Degraded: use rule scores
        return candidates[:8]

    from app.gateway.normalizer import normalize_json
    parsed = normalize_json(result.final_content)
    if parsed and "ranked_candidates" in parsed:
        return parsed["ranked_candidates"][:8]
    return candidates[:8]


async def query_planner_agent(outline_node: dict, scene_plan: dict,
                               required_deps: list, l4_summary: str) -> dict | None:
    """Use LLM to generate structured query plan."""
    prompt = PROMPTS["query_planner"]["system_prompt"]
    user_content = json.dumps({
        "chapter_outline_node": outline_node,
        "scene_plan": scene_plan or {},
        "required_dependencies": required_deps,
        "l4_state_summary": l4_summary,
    }, ensure_ascii=False)

    result = await stream_completion_and_collect(
        system_prompt=prompt,
        user_content=user_content,
        model=AGENT_MODELS["query_planner"],
        temperature=AGENT_TEMPERATURES["query_planner"],
    )

    if result.error or not result.final_content:
        return None  # Use deterministic fallback

    from app.gateway.normalizer import normalize_json
    parsed = normalize_json(result.final_content)
    return parsed


def deterministic_query_template(outline_node: dict, scene_plan: dict,
                                  required_deps: list, l4_st: dict,
                                  current_chapter: int) -> dict:
    """§6.4: Fallback when QueryPlanner API fails."""
    return {
        "required_outline_node_ids": [d.get("target_node_id") for d in required_deps if d.get("required")],
        "character_ids": outline_node.get("involved_character_ids", []),
        "location_ids": [scene_plan.get("location_id")] if scene_plan.get("location_id") else [],
        "item_ids": [],
        "plot_thread_ids": outline_node.get("plot_thread_ids", []),
        "event_types": [],
        "chapter_range": {"from": 1, "to": current_chapter - 1},
        "exact_terms": [],
        "aliases_to_expand": [],
        "semantic_questions": [],
        "max_candidates": 24,
    }
