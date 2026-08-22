"""v9.1 PR-05: True Causal Frontier (spec §14, §15).

Covers:
- get_causal_frontier BFS: hop expansion, cycles, max_nodes truncation,
  relation filtering, priority-relation ordering
- resolve_state_seeds delegation
- causal_frontier_score hop decay
- candidate_merge_and_score causal boost
- deterministic_query_template v2 fields
- causal_frontier_step wiring (no seeds → no-op; failure → [])

All DB access is monkeypatched — no Postgres required.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

import app.engine.causal_retrieval as cr
import app.engine.retrieval as retrieval_mod
from app.engine.causal_retrieval import (
    PRIORITY_RELATIONS,
    causal_frontier_score,
    get_causal_frontier,
)
from app.engine.retrieval import (
    candidate_merge_and_score,
    causal_frontier_step,
    deterministic_query_template,
)

BOOK = uuid.uuid4()


def _ev_id(n: int) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"ev-{n}")


class _GraphEnv:
    """Monkeypatched causal graph: edges + event payloads."""

    def __init__(self, monkeypatch, *, edges, events, state_seeds=None):
        # edges: (src_n, tgt_n, relation, mode, strength)
        self.edges = edges
        self.events = events  # n -> {"event_type": ..., "chapter_no": ...}
        self.state_seeds = state_seeds or []

        async def fake_load_outgoing(db, book_id, source_ids):
            src_set = set(source_ids)
            out = []
            for src_n, tgt_n, rel, mode, strength in self.edges:
                src = _ev_id(src_n)
                if src in src_set:
                    out.append((src, _ev_id(tgt_n), rel.upper(), mode, strength))
            return out

        async def fake_hydrate(db, book_id, ids):
            rows = {}
            for eid in ids:
                n = int(eid.int % 100000)
                payload = self._payload_for(eid)
                if payload is not None:
                    rows[eid] = payload
            return rows

        async def fake_resolve(db, book_id, *, belief_keys=None, goal_keys=None,
                               character_ids=None, limit=12):
            return [s for s in self.state_seeds][:limit]

        monkeypatch.setattr(cr, "_load_outgoing_edges", fake_load_outgoing)
        monkeypatch.setattr(cr, "_load_events_with_chapters", fake_hydrate)
        monkeypatch.setattr(cr, "resolve_state_seeds", fake_resolve)

    def _payload_for(self, eid: uuid.UUID):
        for n, payload in self.events.items():
            if _ev_id(n) == eid:
                return {
                    "event_id": str(eid),
                    "event_type": payload.get("event_type", "decision"),
                    "certainty": payload.get("certainty", "explicit"),
                    "evidence_excerpt": payload.get("evidence_excerpt", "…"),
                    "subject_entity_ids": payload.get("subject_entity_ids", []),
                    "object_entity_ids": payload.get("object_entity_ids", []),
                    "chapter_no": payload.get("chapter_no", 1),
                    "source_type": "story_event",
                }
        return None


@pytest.fixture
def db():
    class _DB:
        async def execute(self, *_a, **_k):
            raise AssertionError("DB should not be hit in unit tests")
    return _DB()


def _node_ids(frontier):
    return {n["event_id"] for n in frontier["nodes"]}


# ── get_causal_frontier BFS ───────────────────────────────────────────

class TestCausalFrontierBFS:
    def test_linear_chain_hops(self, monkeypatch, db):
        return asyncio.run(self._test_linear_chain_hops_impl(monkeypatch, db))

    async def _test_linear_chain_hops_impl(self, monkeypatch, db):
        env = _GraphEnv(
            monkeypatch,
            edges=[
                (1, 2, "CAUSES", "hard", 1.0),
                (2, 3, "MOTIVATES", "hard", 1.0),
                (3, 4, "CAUSES", "hard", 1.0),
            ],
            events={1: {}, 2: {}, 3: {}, 4: {}},
        )
        f = await get_causal_frontier(
            db, book_id=BOOK, seed_event_ids=[str(_ev_id(1))], max_hops=3, max_nodes=24,
        )
        assert _node_ids(f) == {str(_ev_id(i)) for i in (1, 2, 3, 4)}
        by_id = {n["event_id"]: n for n in f["nodes"]}
        assert by_id[str(_ev_id(1))]["hop"] == 0
        assert by_id[str(_ev_id(1))]["seed"] is True
        assert by_id[str(_ev_id(2))]["hop"] == 1
        assert by_id[str(_ev_id(3))]["hop"] == 2
        assert by_id[str(_ev_id(4))]["hop"] == 3
        assert by_id[str(_ev_id(2))]["via_relation"] == "CAUSES"
        assert f["truncated"] is False

    def test_max_hops_limits_expansion(self, monkeypatch, db):
        return asyncio.run(self._test_max_hops_limits_expansion_impl(monkeypatch, db))

    async def _test_max_hops_limits_expansion_impl(self, monkeypatch, db):
        _GraphEnv(
            monkeypatch,
            edges=[
                (1, 2, "CAUSES", "hard", 1.0),
                (2, 3, "CAUSES", "hard", 1.0),
                (3, 4, "CAUSES", "hard", 1.0),
            ],
            events={1: {}, 2: {}, 3: {}, 4: {}},
        )
        f = await get_causal_frontier(
            db, book_id=BOOK, seed_event_ids=[str(_ev_id(1))], max_hops=2,
        )
        assert _node_ids(f) == {str(_ev_id(i)) for i in (1, 2, 3)}

    def test_cycle_does_not_loop(self, monkeypatch, db):
        return asyncio.run(self._test_cycle_does_not_loop_impl(monkeypatch, db))

    async def _test_cycle_does_not_loop_impl(self, monkeypatch, db):
        _GraphEnv(
            monkeypatch,
            edges=[
                (1, 2, "CAUSES", "hard", 1.0),
                (2, 1, "CAUSES", "hard", 1.0),
                (2, 3, "ENABLES", "hard", 1.0),
            ],
            events={1: {}, 2: {}, 3: {}},
        )
        f = await get_causal_frontier(
            db, book_id=BOOK, seed_event_ids=[str(_ev_id(1))], max_hops=5,
        )
        assert _node_ids(f) == {str(_ev_id(i)) for i in (1, 2, 3)}
        assert f["expanded"] == 3

    def test_max_nodes_truncates(self, monkeypatch, db):
        return asyncio.run(self._test_max_nodes_truncates_impl(monkeypatch, db))

    async def _test_max_nodes_truncates_impl(self, monkeypatch, db):
        _GraphEnv(
            monkeypatch,
            edges=[(1, i, "CAUSES", "hard", 1.0) for i in range(2, 12)],
            events={**{1: {}}, **{i: {} for i in range(2, 12)}},
        )
        f = await get_causal_frontier(
            db, book_id=BOOK, seed_event_ids=[str(_ev_id(1))], max_hops=3, max_nodes=5,
        )
        assert len(f["nodes"]) <= 5
        assert f["truncated"] is True

    def test_relation_filter_excludes_others(self, monkeypatch, db):
        return asyncio.run(self._test_relation_filter_excludes_others_impl(monkeypatch, db))

    async def _test_relation_filter_excludes_others_impl(self, monkeypatch, db):
        _GraphEnv(
            monkeypatch,
            edges=[
                (1, 2, "CAUSES", "hard", 1.0),
                (1, 3, "TEMPORAL_BEFORE", "soft", 1.0),
                (1, 4, "ENABLES", "hard", 1.0),
            ],
            events={1: {}, 2: {}, 3: {}, 4: {}},
        )
        f = await get_causal_frontier(
            db, book_id=BOOK, seed_event_ids=[str(_ev_id(1))],
            required_causal_relations=["CAUSES"],
        )
        assert str(_ev_id(2)) in _node_ids(f)
        assert str(_ev_id(3)) not in _node_ids(f)
        assert str(_ev_id(4)) not in _node_ids(f)

    def test_priority_relation_traversal_prefers_priority(self, monkeypatch, db):
        return asyncio.run(self._test_priority_relation_traversal_prefers_priority_impl(monkeypatch, db))

    async def _test_priority_relation_traversal_prefers_priority_impl(self, monkeypatch, db):
        # both edges reachable but node cap allows only one expansion
        _GraphEnv(
            monkeypatch,
            edges=[
                (1, 2, "TEMPORAL_BEFORE", "soft", 0.2),  # non-priority, high strength
                (1, 3, "MOTIVATES", "hard", 0.1),        # priority, low strength
            ],
            events={1: {}, 2: {}, 3: {}},
        )
        f = await get_causal_frontier(
            db, book_id=BOOK, seed_event_ids=[str(_ev_id(1))], max_hops=1, max_nodes=2,
        )
        # priority edge wins the node budget
        assert str(_ev_id(3)) in _node_ids(f)
        assert f["truncated"] is True

    def test_state_seeds_merge_with_explicit(self, monkeypatch, db):
        return asyncio.run(self._test_state_seeds_merge_with_explicit_impl(monkeypatch, db))

    async def _test_state_seeds_merge_with_explicit_impl(self, monkeypatch, db):
        _GraphEnv(
            monkeypatch,
            edges=[(7, 8, "UPDATES_BELIEF", "hard", 1.0)],
            events={7: {}, 8: {}},
            state_seeds=[_ev_id(7)],
        )
        f = await get_causal_frontier(
            db, book_id=BOOK, seed_event_ids=[],
            seed_belief_keys=["B_may_betray"], max_hops=2,
        )
        assert str(_ev_id(7)) in _node_ids(f)
        by_id = {n["event_id"]: n for n in f["nodes"]}
        assert by_id[str(_ev_id(7))]["seed"] is True
        assert f["seed_count"] == 1

    def test_no_seeds_returns_empty(self, db):
        return asyncio.run(self._test_no_seeds_returns_empty_impl(db))

    async def _test_no_seeds_returns_empty_impl(self, db):
        f = await get_causal_frontier(
            db, book_id=BOOK, seed_event_ids=[], seed_belief_keys=[], seed_goal_keys=[],
        )
        assert f["nodes"] == [] and f["edges"] == []
        assert f["seed_count"] == 0 and f["truncated"] is False

    def test_unhydratable_events_skipped(self, monkeypatch, db):
        return asyncio.run(self._test_unhydratable_events_skipped_impl(monkeypatch, db))

    async def _test_unhydratable_events_skipped_impl(self, monkeypatch, db):
        # event 2 has no payload (pruned) — must not crash, just skip
        _GraphEnv(
            monkeypatch,
            edges=[(1, 2, "CAUSES", "hard", 1.0)],
            events={1: {}},
        )
        f = await get_causal_frontier(
            db, book_id=BOOK, seed_event_ids=[str(_ev_id(1))], max_hops=2,
        )
        assert _node_ids(f) == {str(_ev_id(1))}


# ── scoring ───────────────────────────────────────────────────────────

class TestCausalScoring:
    def test_seed_scores_highest(self):
        assert causal_frontier_score({"seed": True, "hop": 0}) == 1000.0

    def test_hop_decay(self):
        s1 = causal_frontier_score({"seed": False, "hop": 1})
        s2 = causal_frontier_score({"seed": False, "hop": 2})
        s3 = causal_frontier_score({"seed": False, "hop": 3})
        assert s1 == 800.0
        assert s2 == 720.0
        assert s3 == 640.0
        assert s1 > s2 > s3

    def test_causal_outranks_keyword_match(self):
        qp = {
            "character_ids": ["c1"],
            "event_types": ["combat"],
            "chapter_range": {"from": 1, "to": 10},
        }
        plain = {
            "event_id": "e-plain", "event_type": "combat", "chapter_no": 9,
            "subject_entity_ids": ["c1"], "object_entity_ids": [],
        }
        causal = {
            "event_id": "e-causal", "event_type": "dialogue", "chapter_no": 2,
            "subject_entity_ids": [], "object_entity_ids": [],
            "causal": True, "hop": 1, "via_relation": "CAUSES",
        }
        scored = candidate_merge_and_score([plain, causal], [], qp)
        assert scored[0]["event_id"] == "e-causal"
        assert scored[0]["reasons"][0].startswith("causal_frontier:CAUSES")

    def test_plain_merge_unchanged_without_causal(self):
        qp = {"character_ids": ["c1"], "event_types": ["combat"],
              "chapter_range": {"from": 1, "to": 10}}
        plain = {
            "event_id": "e-plain", "event_type": "combat", "chapter_no": 9,
            "subject_entity_ids": ["c1"], "object_entity_ids": [],
        }
        scored = candidate_merge_and_score([plain], [], qp)
        assert scored[0]["rule_score"] == SCORE_PLAIN_EXPECTED


SCORE_PLAIN_EXPECTED = 180 + 120 + 19  # character_overlap + event_type + recency(20-(10-9))


# ── deterministic template v2 ────────────────────────────────────────

class TestDeterministicTemplateV2:
    def test_v2_fields_present(self):
        tpl = deterministic_query_template(
            outline_node={"involved_character_ids": ["c1"], "plot_thread_ids": []},
            scene_plan={},
            required_deps=[],
            l4_st={
                "c1": {
                    "state": {
                        "beliefs": {"B_may_betray": {"confidence": 0.8}},
                        "goals": {"G_revenge": {"status": "active"}},
                    }
                }
            },
            current_chapter=5,
        )
        assert tpl["belief_keys"] == ["B_may_betray"]
        assert tpl["goal_keys"] == ["G_revenge"]
        assert tpl["causal_hops"] == 3
        assert tpl["cause_event_ids"] == []
        assert tpl["required_causal_relations"] == []
        assert tpl["knowledge_questions"] == []

    def test_no_l4_state_yields_empty_seeds(self):
        tpl = deterministic_query_template(
            outline_node={}, scene_plan={}, required_deps=[], l4_st={}, current_chapter=3,
        )
        assert tpl["belief_keys"] == [] and tpl["goal_keys"] == []


# ── causal_frontier_step wiring ──────────────────────────────────────

class TestCausalFrontierStep:
    def test_no_seeds_is_noop(self, db):
        return asyncio.run(self._test_no_seeds_is_noop_impl(db))

    async def _test_no_seeds_is_noop_impl(self, db):
        assert await causal_frontier_step(db, BOOK, {"belief_keys": []}, []) == []

    def test_failure_returns_empty_not_raise(self, monkeypatch, db):
        return asyncio.run(self._test_failure_returns_empty_not_raise_impl(monkeypatch, db))

    async def _test_failure_returns_empty_not_raise_impl(self, monkeypatch, db):
        async def boom(*_a, **_k):
            raise RuntimeError("db down")
        monkeypatch.setattr(cr, "get_causal_frontier", boom)
        out = await causal_frontier_step(
            db, BOOK, {"belief_keys": ["B_x"], "causal_hops": 3}, [],
        )
        assert out == []

    def test_passes_plan_seeds(self, monkeypatch, db):
        return asyncio.run(self._test_passes_plan_seeds_impl(monkeypatch, db))

    async def _test_passes_plan_seeds_impl(self, monkeypatch, db):
        calls = {}

        async def fake_frontier(_db, **kw):
            calls.update(kw)
            return {"nodes": [{"event_id": "e1", "causal": True}], "edges": [],
                    "seed_count": 1, "expanded": 1, "truncated": False}

        monkeypatch.setattr(retrieval_mod, "get_causal_frontier", fake_frontier)
        out = await causal_frontier_step(
            db, BOOK,
            {
                "cause_event_ids": ["ev-1"],
                "belief_keys": ["B_x"],
                "goal_keys": ["G_y"],
                "required_causal_relations": ["CAUSES"],
                "causal_hops": 2,
            },
            ["c1"],
        )
        assert out == [{"event_id": "e1", "causal": True}]
        assert calls["seed_event_ids"] == ["ev-1"]
        assert calls["seed_belief_keys"] == ["B_x"]
        assert calls["seed_goal_keys"] == ["G_y"]
        assert calls["required_causal_relations"] == ["CAUSES"]
        assert calls["max_hops"] == 2
        assert calls["seed_character_ids"] == ["c1"]

    def test_invalid_hops_defaults_to_3(self, monkeypatch, db):
        return asyncio.run(self._test_invalid_hops_defaults_to_3_impl(monkeypatch, db))

    async def _test_invalid_hops_defaults_to_3_impl(self, monkeypatch, db):
        calls = {}

        async def fake_frontier(_db, **kw):
            calls.update(kw)
            return {"nodes": [], "edges": [], "seed_count": 0,
                    "expanded": 0, "truncated": False}

        monkeypatch.setattr(retrieval_mod, "get_causal_frontier", fake_frontier)
        await causal_frontier_step(
            db, BOOK, {"belief_keys": ["B_x"], "causal_hops": "NaN"}, [],
        )
        assert calls["max_hops"] == 3


# ── priority relation constants ──────────────────────────────────────

def test_priority_relations_match_spec():
    assert set(PRIORITY_RELATIONS) == {
        "CAUSES", "ENABLES", "MOTIVATES", "UPDATES_BELIEF",
        "TRIGGERS_APPRAISAL", "FRUSTRATES_GOAL", "ACHIEVES_GOAL", "PREVENTS",
    }
