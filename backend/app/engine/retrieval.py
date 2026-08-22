"""SQL-first retrieval engine - §6 v7.3.
9-step fixed retrieval chain with deterministic fallback.

Key fixes:
- event_ledger_search now filters by character_ids, event_types, chapter_range
- candidate_merge_and_score now applies SCORE_WEIGHTS per §6.6
"""
import uuid
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    OutlineDependency, MemoryL4StateSnapshot,
    PlotThread, StoryEvent,
    RetrievalCandidate, Chapter,
)
from app.agents.caller import call_agent
from app.engine.causal_retrieval import causal_frontier_score, get_causal_frontier
from app.engine.chinese_tokenizer import tokenize_for_search
import json
import logging

logger = logging.getLogger("novelforge.retrieval")


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
            OutlineDependency.required,
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
    """Step 5: tsvector full-text search on scene_search_documents.

    Query terms are Chinese-pre-tokenized so they match the same
    unigram/bigram tokens written at index time.
    """
    if not search_terms:
        return []
    query_str = tokenize_for_search(" ".join(search_terms)) or " ".join(search_terms)
    ch_from, ch_to = chapter_range if chapter_range else (0, 10**9)
    result = await db.execute(
        text("""
            SELECT id, chapter_no, scene_no, scene_summary,
                   ts_rank(search_tsv, plainto_tsquery('simple', :q)) as rank
            FROM scene_search_documents
            WHERE book_id = :book_id AND canon_status = 'canon'
              AND chapter_no BETWEEN :ch_from AND :ch_to
              AND search_tsv @@ plainto_tsquery('simple', :q)
            ORDER BY rank DESC
            LIMIT 40
        """),
        {"q": query_str, "book_id": str(book_id), "ch_from": ch_from, "ch_to": ch_to},
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
    - causal_frontier: +800 with hop decay (v9.1 §15)
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

        # v9.1 §15: causal-frontier candidates outrank keyword matches
        if c.get("causal"):
            score += causal_frontier_score(c)
            reasons.append(
                f"causal_frontier:{c.get('via_relation') or 'seed'}:hop{c.get('hop', 1)}"
            )

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


def retrieval_candidate_key(candidate: dict) -> str:
    """Return a stable source key for retrieval audit rows."""
    raw = candidate.get("event_id") or candidate.get("id")
    if raw:
        return str(raw)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(candidate, sort_keys=True, default=str)))


def build_retrieval_candidates(
    retrieval_run_id: uuid.UUID,
    scored: list[dict],
    selected: list[dict],
) -> list[RetrievalCandidate]:
    """Materialize scored and selected candidates for the retrieval audit."""
    selected_keys = {retrieval_candidate_key(candidate) for candidate in selected}
    rows = []
    for rank, candidate in enumerate(scored, start=1):
        source_key = retrieval_candidate_key(candidate)
        try:
            source_id = uuid.UUID(source_key)
        except (ValueError, AttributeError):
            source_id = uuid.uuid5(uuid.NAMESPACE_URL, source_key)
        rows.append(
            RetrievalCandidate(
                id=uuid.uuid4(),
                retrieval_run_id=retrieval_run_id,
                source_type=str(candidate.get("source_type") or "unknown"),
                source_chapter=candidate.get("chapter_no"),
                source_scene=candidate.get("scene_no"),
                source_id=source_id,
                rule_score=float(candidate.get("rule_score") or 0),
                full_text_rank=(float(candidate["rank"]) if candidate.get("rank") is not None else None),
                llm_rank=rank if source_key in selected_keys else None,
                forced=bool(candidate.get("forced")),
                selected=source_key in selected_keys,
                candidate_json=candidate,
            )
        )
    return rows


async def evidence_ranker_agent(
    book_id: uuid.UUID,
    candidates: list,
    semantic_questions: list,
    chapter_goal: str,
    chapter_id: uuid.UUID | None = None,
    **_deprecated,
) -> list[dict]:
    """Step 7: Use LLM via call_agent to rank Top 24 candidates, return Top 8."""
    if not candidates:
        return []
    user_content = json.dumps({
        "candidates": candidates[:24],
        "semantic_questions": semantic_questions,
        "chapter_goal": chapter_goal,
    }, ensure_ascii=False)

    try:
        run, publishable, meta = await call_agent(
            book_id=book_id,
            agent_role="evidence_ranker",
            user_content=user_content,
            chapter_id=chapter_id,
            assembly_manifest={
                "entries": [
                    {"type": "retrieval_candidates", "count": min(24, len(candidates))},
                    {
                        "type": "story_evidence_refs",
                        "ids": [
                            c.get("id") or c.get("event_id")
                            for c in candidates[:24]
                            if c.get("id") or c.get("event_id")
                        ],
                    },
                ],
                "excluded_entries": [],
                "budget": {"max_context": 128000, "reserved_output": 2048, "used": len(user_content) // 4},
            },
        )
    except Exception:
        return candidates[:8]

    if meta.get("error") or publishable is None:
        return candidates[:8]

    if isinstance(publishable, dict) and "ranked_candidates" in publishable:
        return publishable["ranked_candidates"][:8]
    if isinstance(publishable, str):
        from app.gateway.normalizer import normalize_json
        parsed = normalize_json(publishable)
        if parsed and "ranked_candidates" in parsed:
            return parsed["ranked_candidates"][:8]
    return candidates[:8]


async def query_planner_agent(
    book_id: uuid.UUID,
    outline_node: dict,
    scene_plan: dict,
    required_deps: list,
    l4_summary: str,
    chapter_id: uuid.UUID | None = None,
    l4_refs: list | None = None,
    **_deprecated,
) -> dict | None:
    """Use LLM via call_agent to generate structured query plan."""
    user_content = json.dumps({
        "chapter_outline_node": outline_node,
        "scene_plan": scene_plan or {},
        "required_dependencies": required_deps,
        "l4_state_summary": l4_summary,
    }, ensure_ascii=False)

    try:
        run, publishable, meta = await call_agent(
            book_id=book_id,
            agent_role="query_planner",
            user_content=user_content,
            chapter_id=chapter_id,
            l4_refs=l4_refs or [],
            assembly_manifest={
                "entries": [
                    {"type": "outline_node"},
                    {"type": "l4_summary"},
                    {"type": "required_deps", "count": len(required_deps or [])},
                ],
                "excluded_entries": [],
                "budget": {"max_context": 128000, "reserved_output": 2048, "used": len(user_content) // 4},
            },
        )
    except Exception:
        return None

    if meta.get("error") or publishable is None:
        return None

    if isinstance(publishable, dict):
        return {
            **publishable,
            "_agent_run_id": str(run.id),
            "_prompt_version": run.prompt_version,
            "_model_name": run.model_name,
        }
    if isinstance(publishable, str):
        from app.gateway.normalizer import normalize_json
        parsed = normalize_json(publishable)
        if parsed is None:
            return None
        return {
            **parsed,
            "_agent_run_id": str(run.id),
            "_prompt_version": run.prompt_version,
            "_model_name": run.model_name,
        }
    return None


def deterministic_query_template(outline_node: dict, scene_plan: dict,
                                  required_deps: list, l4_st: dict,
                                  current_chapter: int) -> dict:
    """§6.4: Fallback when QueryPlanner API fails."""
    # v9.1 §14: derive causal seeds from L4 state structure — keys that
    # actually exist, never invented ones.
    belief_keys: list[str] = []
    goal_keys: list[str] = []
    core_anchor_ids: list[str] = []
    for payload in (l4_st or {}).values():
        state = payload.get("state") if isinstance(payload, dict) else payload
        if not isinstance(state, dict):
            continue
        beliefs = state.get("beliefs")
        if isinstance(beliefs, dict):
            belief_keys.extend(list(beliefs.keys())[:6])
        goals = state.get("goals")
        if isinstance(goals, dict):
            goal_keys.extend(list(goals.keys())[:4])
        anchors = state.get("core_anchor_ids")
        if isinstance(anchors, list):
            core_anchor_ids.extend(str(a) for a in anchors[:4])

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
        "core_anchor_ids": core_anchor_ids,
        "belief_keys": belief_keys,
        "goal_keys": goal_keys,
        "cause_event_ids": [],
        "required_causal_relations": [],
        "knowledge_questions": [],
        "causal_hops": 3,
        "max_candidates": 24,
    }


async def causal_frontier_step(
    db: AsyncSession,
    book_id: uuid.UUID,
    query_plan: dict,
    character_ids: list,
) -> list[dict]:
    """v9.1 §15: expand the true causal frontier and return frontier nodes
    as retrieval candidates. Returns [] when the plan carries no causal seeds.
    """
    seed_event_ids = list(query_plan.get("cause_event_ids") or [])
    belief_keys = list(query_plan.get("belief_keys") or [])
    goal_keys = list(query_plan.get("goal_keys") or [])
    if not seed_event_ids and not belief_keys and not goal_keys:
        return []
    relations = query_plan.get("required_causal_relations") or None
    try:
        hops = int(query_plan.get("causal_hops") or 3)
    except (TypeError, ValueError):
        hops = 3
    try:
        frontier = await get_causal_frontier(
            db,
            book_id=book_id,
            seed_event_ids=seed_event_ids,
            seed_belief_keys=belief_keys,
            seed_goal_keys=goal_keys,
            seed_character_ids=character_ids,
            max_hops=hops,
            max_nodes=24,
            required_causal_relations=relations,
        )
    except Exception:
        logger.exception("causal frontier expansion failed, continuing without it")
        return []
    nodes = frontier.get("nodes") or []
    if nodes:
        logger.info(
            "causal frontier: %s node(s), %s edge(s), seeds=%s truncated=%s",
            len(nodes), len(frontier.get("edges") or []),
            frontier.get("seed_count"), frontier.get("truncated"),
        )
    return nodes
