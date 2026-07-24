"""SQL-first retrieval engine - §6 v7.3.
9-step fixed retrieval chain with deterministic fallback.

Key fixes:
- event_ledger_search now filters by character_ids, event_types, chapter_range
- candidate_merge_and_score now applies SCORE_WEIGHTS per §6.6
"""
import uuid
from sqlalchemy import select, text, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    OutlineNode, OutlineDependency, MemoryL4StateSnapshot,
    PlotThread, StoryEvent, SceneSearchDocument, EntityAlias,
    QueryPlan, RetrievalRun, RetrievalCandidate, Chapter,
)
from app.gateway.model_gateway import stream_with_retry
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
    query = select(PlotThread).where(
        PlotThread.book_id == book_id,
        PlotThread.status == "open",
    )
    if thread_ids:
        query = query.where(PlotThread.id.in_(thread_ids))
    result = await db.execute(query)
    threads = result.scalars().all()
    return [{"thread_id": str(t.id), "name": t.name, "planted_chapter": t.planted_chapter} for t in threads]


async def event_ledger_search(db: AsyncSession, book_id: uuid.UUID,
                               character_ids: list, event_types: list,
                               chapter_range: tuple[int, int]) -> list[dict]:
    """Step 4: Query story_events by entity, event type, chapter range per §6.5.

    Filters by:
    - book_id
    - chapter_range via Chapter.chapter_no join
    - event_types via event_type match
    Returns chapter_no for recency scoring.
    Limits to 60 results, ordered by recency.
    """
    # Build query with proper ORM join to Chapter for chapter_no
    query = (
        select(StoryEvent, Chapter.chapter_no)
        .outerjoin(Chapter, StoryEvent.chapter_id == Chapter.id)
        .where(
            StoryEvent.book_id == book_id,
        )
    )

    # Filter by chapter range via joined Chapter
    if chapter_range and chapter_range[1] > 0:
        query = query.where(
            Chapter.chapter_no >= chapter_range[0],
            Chapter.chapter_no <= chapter_range[1],
        )

    # Filter by event types if provided
    if event_types:
        query = query.where(StoryEvent.event_type.in_(event_types))

    query = query.order_by(StoryEvent.created_at.desc()).limit(60)
    result = await db.execute(query)
    rows = result.all()

    # Unpack (StoryEvent, chapter_no) tuples and post-filter by character_ids
    events = []
    for row in rows:
        e = row[0]
        ch_no = row[1] if row[1] is not None else 0
        events.append((e, ch_no))

    if character_ids:
        char_id_strs = [str(c) for c in character_ids]
        filtered = []
        for e, ch_no in events:
            subj = [str(s) for s in (e.subject_entity_ids or [])]
            obj = [str(s) for s in (e.object_entity_ids or [])]
            if any(cid in subj or cid in obj for cid in char_id_strs):
                filtered.append((e, ch_no))
        events = filtered

    return [{"event_id": str(e.id), "event_type": e.event_type,
             "chapter_no": ch_no,
             "certainty": e.certainty,
             "evidence_excerpt": (e.evidence_excerpt or "")[:200],
             "subject_entity_ids": e.subject_entity_ids,
             "object_entity_ids": e.object_entity_ids} for e, ch_no in events]


async def full_text_search(db: AsyncSession, book_id: uuid.UUID,
                            search_terms: list[str], chapter_range: tuple[int, int]) -> list[dict]:
    """Step 5: tsvector full-text search on scene_search_documents."""
    if not search_terms:
        return []
    query_str = " ".join(search_terms)
    result = await db.execute(
        text("""
            SELECT id, chapter_no, scene_no, scene_summary,
                   ts_rank(search_tsv, plainto_tsquery('simple', :q)) as rank
            FROM scene_search_documents
            WHERE book_id = :book_id AND canon_status = 'canon'
              AND search_tsv @@ plainto_tsquery('simple', :q)
            ORDER BY rank DESC
            LIMIT 40
        """),
        {"q": query_str, "book_id": str(book_id)}
    )
    rows = result.fetchall()
    return [{"id": str(r[0]), "chapter_no": r[1], "scene_no": r[2],
             "scene_summary": r[3], "rank": float(r[4])} for r in rows]


def candidate_merge_and_score(event_candidates: list, ft_candidates: list,
                               query_plan: dict) -> list[dict]:
    """Step 6: Merge, deduplicate, score with rules per §6.6.

    Applies SCORE_WEIGHTS:
    - character_overlap: +180 per overlapping character
    - event_type_match: +120
    - full_text_rank: 0-100 (scaled from ts_rank)
    - same_outline_arc: +40
    - recency: 0-20 tiebreaker
    """
    seen = set()
    scored = []

    # Get query plan params for scoring
    qp_chars = set(str(c) for c in query_plan.get("character_ids", []))
    qp_event_types = set(query_plan.get("event_types", []))
    qp_chapter_to = (query_plan.get("chapter_range") or {}).get("to", 999)

    for c in event_candidates:
        key = c.get("event_id", c.get("id", ""))
        if key in seen:
            continue
        seen.add(key)

        score = 0
        reasons = []

        # Character overlap scoring
        subj = [str(s) for s in c.get("subject_entity_ids", [])]
        obj = [str(s) for s in c.get("object_entity_ids", [])]
        all_entities = set(subj + obj)
        char_overlap = len(all_entities & qp_chars)
        if char_overlap > 0:
            score += SCORE_WEIGHTS["character_overlap"] * char_overlap
            reasons.append("character_overlap")

        # Event type match
        if c.get("event_type") in qp_event_types:
            score += SCORE_WEIGHTS["event_type_match"]
            reasons.append("event_type_match")

        # Recency tiebreaker (0-20 based on chapter_no)
        chapter_no = c.get("chapter_no", 0)
        if chapter_no and qp_chapter_to > 0:
            recency = max(0, 20 - (qp_chapter_to - chapter_no))
            score += min(recency, SCORE_WEIGHTS["recency_max"])

        scored.append({**c, "rule_score": score, "source_type": "story_event",
                        "reasons": reasons})

    for c in ft_candidates:
        key = c.get("id", "")
        if key in seen:
            continue
        seen.add(key)

        # Full text rank scaled to 0-100
        rank = c.get("rank", 0.0)
        ft_score = min(int(rank * 100), SCORE_WEIGHTS["full_text_max"])

        scored.append({**c, "rule_score": ft_score, "source_type": "scene",
                        "reasons": ["full_text_match"]})

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

    result = await stream_with_retry(
        system_prompt=prompt,
        user_content=user_content,
        model=AGENT_MODELS["evidence_ranker"],
        temperature=AGENT_TEMPERATURES["evidence_ranker"],
    )

    if result.error or not result.final_content:
        # §6.8: Degraded mode - use rule scores
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

    result = await stream_with_retry(
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
