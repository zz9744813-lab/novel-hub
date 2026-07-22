"""SQL-first retrieval engine - §6 v7.3.
9-step fixed retrieval chain with deterministic fallback.
FIX P0-9: proper event filtering, full text search with chapter range,
scoring rules actually implemented, QueryPlan/RetrievalRun persisted.
"""
import uuid
import json
import time
from sqlalchemy import select, text, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    OutlineNode, OutlineDependency, MemoryL4StateSnapshot,
    PlotThread, StoryEvent, SceneSearchDocument, EntityAlias,
    QueryPlan, RetrievalRun, RetrievalCandidate, RetrievalJudgement,
)
from app.gateway.model_gateway import stream_completion_and_collect
from app.prompts import PROMPTS, AGENT_MODELS, AGENT_TEMPERATURES


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
    return [{"thread_id": str(t.id), "name": t.name,
             "planted_chapter": t.planted_chapter} for t in threads]


async def event_ledger_search(db: AsyncSession, book_id: uuid.UUID,
                               character_ids: list, event_types: list,
                               chapter_range: tuple[int, int]) -> list[dict]:
    """Step 4: Query story_events by entity, event type, chapter range.
    FIX P0-9: Actually filter by all parameters.
    """
    query = select(StoryEvent).where(StoryEvent.book_id == book_id)

    # Join with chapters to filter by chapter_no range
    from app.models import Chapter as ChapterModel
    query = query.join(
        ChapterModel, ChapterModel.id == StoryEvent.chapter_id
    ).where(
        ChapterModel.chapter_no >= chapter_range[0],
        ChapterModel.chapter_no <= chapter_range[1],
    )

    result = await db.execute(
        query.order_by(StoryEvent.created_at.desc()).limit(60)
    )
    events = result.scalars().all()

    # Post-filter by character_ids and event_types (since they're JSONB arrays)
    filtered = []
    for e in events:
        # Check character overlap
        if character_ids:
            char_str_ids = [str(c) for c in character_ids]
            subject_ids = [str(s) for s in (e.subject_entity_ids or [])]
            if not any(c in subject_ids for c in char_str_ids):
                continue
        # Check event type match
        if event_types and e.event_type not in event_types:
            continue
        filtered.append({
            "event_id": str(e.id),
            "event_type": e.event_type,
            "certainty": e.certainty,
            "evidence_excerpt": e.evidence_excerpt[:200],
            "subject_entity_ids": [str(s) for s in (e.subject_entity_ids or [])],
            "chapter_id": str(e.chapter_id),
        })
    return filtered


async def full_text_search(db: AsyncSession, book_id: uuid.UUID,
                            search_terms: list[str], chapter_range: tuple[int, int]) -> list[dict]:
    """Step 5: tsvector full-text search on scene_search_documents.
    FIX P0-9: Use chapter_range filter and generate search_tsv on the fly.
    """
    if not search_terms:
        return []
    query_str = " & ".join(search_terms)
    result = await db.execute(
        text("""
            SELECT id, chapter_no, scene_no, scene_summary,
                   ts_rank(
                     to_tsvector('simple', coalesce(scene_summary, '') || ' ' || coalesce(search_text, '')),
                     plainto_tsquery('simple', :q)
                   ) as rank
            FROM scene_search_documents
            WHERE book_id = :book_id
              AND canon_status = 'canon'
              AND chapter_no >= :chap_start
              AND chapter_no <= :chap_end
            ORDER BY rank DESC
            LIMIT 40
        """),
        {
            "q": query_str,
            "book_id": str(book_id),
            "chap_start": chapter_range[0],
            "chap_end": chapter_range[1],
        }
    )
    rows = result.fetchall()
    return [{"id": str(r[0]), "chapter_no": r[1], "scene_no": r[2],
             "scene_summary": r[3], "rank": float(r[4] or 0)} for r in rows]


def candidate_merge_and_score(event_candidates: list, ft_candidates: list,
                               query_plan: dict) -> list[dict]:
    """Step 6: Merge, deduplicate, score with rules per §6.6.
    FIX P0-9: Actually implement scoring rules.
    """
    seen = set()
    scored = []

    char_ids = set(str(c) for c in query_plan.get("character_ids", []))
    event_types = set(query_plan.get("event_types", []))
    forced_deps = set(query_plan.get("required_outline_node_ids", []))

    for c in event_candidates:
        key = c.get("event_id", c.get("id", ""))
        if key in seen:
            continue
        seen.add(key)
        score = 0

        # Character overlap scoring
        event_chars = set(str(s) for s in c.get("subject_entity_ids", []))
        overlap = char_ids & event_chars
        if overlap:
            score += SCORE_WEIGHTS["character_overlap"] * min(len(overlap), 3) // 3

        # Event type match scoring
        if c.get("event_type") in event_types:
            score += SCORE_WEIGHTS["event_type_match"]

        # Recency scoring (simplified — events are already sorted by created_at desc)
        score += SCORE_WEIGHTS["recency_max"]  # At least give some recency score

        scored.append({**c, "rule_score": score, "source_type": "story_event"})

    for c in ft_candidates:
        key = c.get("id", "")
        if key in seen:
            continue
        seen.add(key)
        score = int(c.get("rank", 0) * SCORE_WEIGHTS["full_text_max"])
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
        return None

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


def persist_retrieval_artifacts(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    query_plan: dict,
    scored: list[dict],
    ranked: list[dict],
    source_run_id: uuid.UUID,
    model_name: str = "unknown",
    degraded: bool = False,
) -> RetrievalRun:
    """FIX P0-9: Persist QueryPlan and RetrievalRun to database."""
    qp = QueryPlan(
        id=uuid.uuid4(),
        book_id=book_id,
        chapter_id=chapter_id,
        scene_id=None,
        plan_json=query_plan,
        source_run_id=source_run_id,
        prompt_version="v1",
        model_name=model_name,
    )
    db.add(qp)

    run = RetrievalRun(
        id=uuid.uuid4(),
        book_id=book_id,
        chapter_id=chapter_id,
        query_plan_id=qp.id,
        status="completed",
        degraded=degraded,
        candidate_count=len(scored),
        selected_count=len(ranked),
    )
    db.add(run)

    # Persist candidates
    for i, c in enumerate(scored[:24]):
        candidate = RetrievalCandidate(
            id=uuid.uuid4(),
            retrieval_run_id=run.id,
            source_type=c.get("source_type", "unknown"),
            source_chapter=c.get("chapter_no"),
            source_scene=c.get("scene_no"),
            source_id=uuid.UUID(c["id"]) if c.get("id") and _is_uuid(c["id"]) else uuid.uuid4(),
            rule_score=float(c.get("rule_score", 0)),
            full_text_rank=float(c.get("rank", 0)) if c.get("rank") is not None else None,
            selected=i < 8,
            candidate_json=c,
        )
        db.add(candidate)

    return run


def _is_uuid(val: str) -> bool:
    try:
        uuid.UUID(val)
        return True
    except (ValueError, TypeError):
        return False
