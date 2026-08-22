"""v9.1 True Causal Frontier retrieval (spec §14, §15).

BFS / limited traversal over the committed StoryEventEdge causal graph —
NOT keyword search pretending to be causality.

    seeds = explicit event ids ∪ events touching belief/goal paths
    frontier = BFS(seeds, max_hops, relation priority, max_nodes)

Priority relations (§15) are expanded first; other relations
(e.g. TEMPORAL_BEFORE) are followed only when budget remains.
Deterministic ordering everywhere — no LLM, no randomness.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StoryEventEdge

logger = logging.getLogger("novelforge.causal_retrieval")

# §15 priority relations — expanded first during BFS
PRIORITY_RELATIONS = (
    "CAUSES",
    "ENABLES",
    "MOTIVATES",
    "UPDATES_BELIEF",
    "TRIGGERS_APPRAISAL",
    "FRUSTRATES_GOAL",
    "ACHIEVES_GOAL",
    "PREVENTS",
)

DEFAULT_MAX_HOPS = 3
DEFAULT_MAX_NODES = 24
_SEED_SCAN_LIMIT = 120


def _as_uuids(raw: list | None) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    for v in raw or []:
        try:
            out.append(uuid.UUID(str(v)))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


def _esc(like: str) -> str:
    return like.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _load_events_with_chapters(
    db: AsyncSession, book_id: uuid.UUID, event_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """Batch-load StoryEvents joined to their chapter_no."""
    rows: dict[uuid.UUID, dict] = {}
    for i in range(0, len(event_ids), 60):
        batch = event_ids[i : i + 60]
        result = await db.execute(
            sql_text(
                """
                SELECT e.id, e.event_type, e.certainty, e.evidence_excerpt,
                       e.subject_entity_ids, e.object_entity_ids, c.chapter_no
                FROM story_events e
                JOIN chapters c ON e.chapter_id = c.id
                WHERE e.book_id = :book_id AND e.id = ANY(:ids)
                """
            ),
            {"book_id": str(book_id), "ids": [str(x) for x in batch]},
        )
        for r in result.fetchall():
            eid = uuid.UUID(str(r[0]))
            rows[eid] = {
                "event_id": str(eid),
                "event_type": r[1],
                "certainty": r[2],
                "evidence_excerpt": (r[3] or "")[:200],
                "subject_entity_ids": r[4] or [],
                "object_entity_ids": r[5] or [],
                "chapter_no": r[6] or 0,
                "source_type": "story_event",
            }
    return rows


async def resolve_state_seeds(
    db: AsyncSession,
    book_id: uuid.UUID,
    *,
    belief_keys: list[str] | None = None,
    goal_keys: list[str] | None = None,
    character_ids: list | None = None,
    limit: int = 12,
) -> list[uuid.UUID]:
    """Resolve belief/goal keys into recent StoryEvent ids that touched them.

    Matches the key substring inside after_state/before_state field paths,
    optionally scoped to one character per key ("<cid>.<key>" form).
    """
    patterns: list[tuple[str, str]] = []
    for key in (belief_keys or [])[:8]:
        if not key:
            continue
        patterns.append(("belief", str(key)))
    for key in (goal_keys or [])[:8]:
        if not key:
            continue
        patterns.append(("goal", str(key)))
    if not patterns:
        return []

    char_ids = _as_uuids(character_ids)
    found: dict[uuid.UUID, int] = {}
    for _kind, key in patterns:
        like = f"%{_esc(key)}%"
        result = await db.execute(
            sql_text(
                """
                SELECT e.id
                FROM story_events e
                WHERE e.book_id = :book_id
                  AND e.certainty = 'explicit'
                  AND (e.after_state::text ILIKE :like OR e.before_state::text ILIKE :like)
                ORDER BY e.created_at DESC
                LIMIT :lim
                """
            ),
            {"book_id": str(book_id), "like": like, "lim": _SEED_SCAN_LIMIT},
        )
        for r in result.fetchall():
            try:
                eid = uuid.UUID(str(r[0]))
            except (ValueError, TypeError):
                continue
            found[eid] = found.get(eid, 0) + 1
        if char_ids:
            # prefer seeds tied to the scoped characters when both exist
            for cid in char_ids[:6]:
                scoped = f"%{_esc(str(cid))}%{_esc(key)}%"
                result = await db.execute(
                    sql_text(
                        """
                        SELECT e.id
                        FROM story_events e
                        WHERE e.book_id = :book_id
                          AND e.certainty = 'explicit'
                          AND (e.after_state::text ILIKE :like
                               OR e.before_state::text ILIKE :like)
                        ORDER BY e.created_at DESC
                        LIMIT :lim
                        """
                    ),
                    {"book_id": str(book_id), "like": scoped, "lim": 24},
                )
                for r in result.fetchall():
                    try:
                        eid = uuid.UUID(str(r[0]))
                    except (ValueError, TypeError):
                        continue
                    found[eid] = found.get(eid, 0) + 2
    if not found:
        return []
    ranked = sorted(found.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return [eid for eid, _ in ranked[:limit]]


async def _load_outgoing_edges(
    db: AsyncSession, book_id: uuid.UUID, source_ids: list[uuid.UUID]
) -> list[tuple[uuid.UUID, uuid.UUID, str, str, float | None]]:
    """Load outgoing StoryEventEdges for a batch of source events.

    Returns tuples of (source, target, relation, mode, strength).
    """
    out: list[tuple[uuid.UUID, uuid.UUID, str, str, float | None]] = []
    for i in range(0, len(source_ids), 60):
        batch = source_ids[i : i + 60]
        result = await db.execute(
            select(StoryEventEdge).where(
                StoryEventEdge.book_id == book_id,
                StoryEventEdge.source_event_id.in_(batch),
            )
        )
        for e in result.scalars():
            out.append(
                (
                    e.source_event_id,
                    e.target_event_id,
                    (e.relation_type or "").upper(),
                    (e.edge_mode or "soft").lower(),
                    e.strength,
                )
            )
    return out


async def get_causal_frontier(
    db: AsyncSession,
    *,
    book_id: uuid.UUID,
    seed_event_ids: list | None = None,
    seed_belief_keys: list[str] | None = None,
    seed_goal_keys: list[str] | None = None,
    seed_character_ids: list | None = None,
    max_hops: int = DEFAULT_MAX_HOPS,
    max_nodes: int = DEFAULT_MAX_NODES,
    required_causal_relations: list[str] | None = None,
) -> dict[str, Any]:
    """BFS over StoryEventEdge from seeds (spec §15).

    Returns:
        {
            "nodes": [ {...event fields, "hop": 0..N, "seed": bool,
                        "via_relation": str|None, "score": float} ],
            "edges": [ {"from": id, "to": id, "relation": str, "mode": str,
                        "hop": int} ],
            "seed_count": int,
            "expanded": int,
            "truncated": bool,
        }
    """
    max_hops = max(1, min(int(max_hops or DEFAULT_MAX_HOPS), 6))
    max_nodes = max(1, min(int(max_nodes or DEFAULT_MAX_NODES), 64))
    relation_filter = None
    if required_causal_relations:
        relation_filter = {str(r).strip().upper() for r in required_causal_relations if r}
        relation_filter = {r for r in relation_filter if r}

    # 1) seed set: explicit ids ∪ state-path seeds
    seeds = _as_uuids(seed_event_ids)
    explicit_seed_ids = set(seeds)
    state_seeds = await resolve_state_seeds(
        db,
        book_id,
        belief_keys=seed_belief_keys,
        goal_keys=seed_goal_keys,
        character_ids=seed_character_ids,
    )
    for eid in state_seeds:
        if eid not in seeds:
            seeds.append(eid)
    if not seeds:
        return {"nodes": [], "edges": [], "seed_count": 0, "expanded": 0, "truncated": False}

    # 2) batch-load all edges adjacent to any node we ever visit
    nodes: dict[uuid.UUID, dict] = {}
    edges_out: list[dict] = []
    visited: set[uuid.UUID] = set()
    truncated = False

    current_frontier: list[uuid.UUID] = []
    for eid in seeds:
        if eid not in visited:
            visited.add(eid)
            current_frontier.append(eid)

    hop = 0
    while current_frontier and hop < max_hops and len(visited) < max_nodes:
        next_frontier: list[uuid.UUID] = []
        hop_edges: dict[uuid.UUID, list[dict]] = {}
        raw_edges = await _load_outgoing_edges(db, book_id, current_frontier)
        for src, tgt, rel, mode, strength in raw_edges:
            if relation_filter and rel not in relation_filter:
                continue
            hop_edges.setdefault(src, []).append(
                {
                    "to": tgt,
                    "relation": rel,
                    "mode": mode,
                    "strength": strength,
                    "is_priority": rel in PRIORITY_RELATIONS,
                }
            )

        for src in current_frontier:
            out = hop_edges.get(src, [])
            # priority relations first, then strength desc, then id for stability
            out.sort(key=lambda x: (not x["is_priority"], -(x["strength"] or 0), str(x["to"])))
            for edge in out:
                tgt = edge["to"]
                edges_out.append(
                    {
                        "from": str(src),
                        "to": str(tgt),
                        "relation": edge["relation"],
                        "mode": edge["mode"],
                        "hop": hop + 1,
                    }
                )
                if tgt in visited:
                    continue
                if len(visited) >= max_nodes:
                    truncated = True
                    continue
                visited.add(tgt)
                next_frontier.append(tgt)

        current_frontier = next_frontier
        hop += 1

    # 3) hydrate node payloads
    all_ids = list(visited)
    hydrated = await _load_events_with_chapters(db, book_id, all_ids)
    seed_set = explicit_seed_ids | set(state_seeds)
    nodes = []
    for eid in all_ids:
        payload = hydrated.get(eid)
        if payload is None:
            continue  # event pruned / not canon-reachable; skip
        nodes.append(
            {
                **payload,
                "hop": 0 if eid in seed_set else _hop_of(eid, edges_out),
                "seed": eid in seed_set,
                "via_relation": _via_relation(eid, edges_out),
                "causal": True,
            }
        )
    nodes.sort(key=lambda n: (n["hop"], not n["seed"], n["chapter_no"], n["event_id"]))

    return {
        "nodes": nodes,
        "edges": edges_out,
        "seed_count": len(seed_set & visited),
        "expanded": len(visited),
        "truncated": truncated,
    }


def _hop_of(eid: uuid.UUID, edges: list[dict]) -> int:
    best = 99
    for e in edges:
        if e["to"] == str(eid):
            best = min(best, e["hop"])
    return best if best < 99 else 1


def _via_relation(eid: uuid.UUID, edges: list[dict]) -> str | None:
    for e in edges:
        if e["to"] == str(eid):
            return e["relation"]
    return None


def causal_frontier_score(node: dict) -> float:
    """Score a frontier node: priority-relation proximity wins, hops decay."""
    base = 800.0
    if node.get("seed"):
        return 1000.0
    hop = int(node.get("hop") or 1)
    return max(0.0, base - 80.0 * (hop - 1))
