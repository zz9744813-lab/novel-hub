"""v9.0 Counterfactual Audit (spec §33, §34).

Deterministic structural replay — no LLM here (P0 requirement):

    base = clone(state_before)
    actual         = replay(base, do(all_events))
    counterfactual = replay(base, do(all_events minus E))
    diff(actual, counterfactual)

Only key nodes are audited (§33.1); classification per §33.2:
- necessary_support      — target loses all support when E removed
- contributing_support   — target keeps other support sources
- motivation_redundancy  — removal changes nothing yet E framed as motive
- false_causal_emphasis  — removal changes nothing and E declared hard-causal
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.narrative import (
    CounterfactualFinding,
    CounterfactualReport,
    ProvisionalEvent,
)
from app.engine.narrative_state import apply_state_deltas, normalize_state

logger = logging.getLogger("novelforge.counterfactual")

# §33.1 key-node triggers
KEY_NODE_EVENT_TYPES = (
    "betrayal",
    "death",
    "identity_reveal",
    "pivotal_decision",
    "trauma",
)
KEY_NODE_KEYWORDS = {
    "betrayal": ("betray", "背叛", "出卖"),
    "death": ("death", "die", "kill", "死亡", "阵亡", "杀死"),
    "identity_reveal": ("identity", "reveal", "身世", "身份揭露"),
    "pivotal_decision": ("decide", "decision", "抉择", "决定"),
    "trauma": ("trauma", "创伤"),
}
SUPPORT_RELATIONS = {"MOTIVATES", "INTENDS", "CAUSES", "ENABLES", "ACHIEVES_GOAL"}
MOTIVE_RELATIONS = {"MOTIVATES", "INTENDS"}

MAX_KEY_NODES_PER_AUDIT = 12
RELATIONSHIP_PATH_PREFIX = "relationships."


def _event_text(ev: ProvisionalEvent) -> str:
    return f"{ev.event_type or ''} {ev.action}".lower()


def _effect_paths(ev: ProvisionalEvent) -> list[str]:
    return [eff.path for eff in ev.hard_effects]


def is_key_node_event(ev: ProvisionalEvent) -> bool:
    """Deterministic trigger detection (§33.1). No LLM."""
    text = _event_text(ev)
    for kw in KEY_NODE_KEYWORDS.values():
        if any(k in text for k in kw):
            return True
    if (ev.event_type or "").lower() in KEY_NODE_EVENT_TYPES:
        return True
    paths = _effect_paths(ev)
    if any(p.startswith(RELATIONSHIP_PATH_PREFIX) for p in paths):
        return True  # relationship_delta
    if any(p.startswith("goals.") for p in paths):
        return True  # major_goal_created / abandoned
    if any(p.startswith("abilities.") or p.startswith("power.") for p in paths):
        return True  # major_power_change
    return False


def _collect_contracts(contracts: list[dict]) -> tuple[list[dict], list[dict]]:
    """Flatten contracts into ordered event dicts and edge dicts."""
    events: list[dict] = []
    edges: list[dict] = []
    for c in contracts or []:
        if not isinstance(c, dict):
            continue
        for ev in c.get("events") or []:
            if isinstance(ev, dict) and ev.get("event_key"):
                events.append(ev)
        for e in c.get("causal_edges") or []:
            if not isinstance(e, dict):
                continue
            frm = str(e.get("from") or e.get("from_key") or "")
            to = str(e.get("to") or e.get("to_key") or "")
            if frm and to:
                edges.append(
                    {
                        "from": frm,
                        "to": to,
                        "relation": str(e.get("relation") or "CAUSES").upper(),
                        "mode": str(e.get("mode") or "soft").lower(),
                    }
                )
    return events, edges


def _replay(
    states: dict[str, dict[str, Any]],
    events: list[dict],
    removed_key: str | None,
) -> dict[str, dict[str, Any]]:
    """Clone + apply hard effects of every event except removed_key."""
    working = {k: normalize_state(v) for k, v in states.items()}
    for ev in events:
        if removed_key and ev.get("event_key") == removed_key:
            continue
        for eff in ev.get("hard_effects") or []:
            if not isinstance(eff, dict) or str(eff.get("mode") or "hard") != "hard":
                continue
            path = str(eff.get("path") or "")
            if not path:
                continue
            head = path.split(".")[0]
            if head in working and "." in path:
                rel = dict(eff)
                rel["path"] = path.split(".", 1)[1]
                working[head] = apply_state_deltas(working[head], [rel])
            elif head in working:
                continue
            else:
                for k in list(working.keys()):
                    working[k] = apply_state_deltas(working[k], [eff])
    return working


def _state_diff(
    actual: dict[str, dict[str, Any]],
    counterfactual: dict[str, dict[str, Any]],
    paths: list[str],
) -> list[str]:
    """Return paths whose replayed value differs between the two worlds.

    Paths are char-headed ("{char_id}.x.y"); non-char heads are compared
    against every character slice.
    """
    changed: list[str] = []

    def _get(state: dict, path: str) -> Any:
        cur: Any = state
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        return cur

    for p in paths:
        head = p.split(".")[0]
        rel = p.split(".", 1)[1] if "." in p else p
        chars = [head] if head in actual or head in counterfactual else list(
            set(actual.keys()) | set(counterfactual.keys())
        )
        for ch in chars:
            a, c = actual.get(ch) or {}, counterfactual.get(ch) or {}
            if _get(a, rel) != _get(c, rel):
                changed.append(f"{ch}.{rel}" if ch != head else p)
    return changed


def audit_counterfactual(
    contracts: list[dict],
    states_by_char: dict[str, dict[str, Any]] | None = None,
) -> CounterfactualReport:
    """Pure structural audit over compiled scene contracts."""
    report = CounterfactualReport()
    events, edges = _collect_contracts(contracts)
    if not events:
        return report

    base_states = states_by_char or {}
    actual = _replay(base_states, events, None)
    out_edges: dict[str, list[dict]] = {}
    in_edges: dict[str, list[dict]] = {}
    for e in edges:
        out_edges.setdefault(e["from"], []).append(e)
        in_edges.setdefault(e["to"], []).append(e)

    key_nodes = [ev for ev in events if is_key_node_event(_as_provisional(ev))][
        :MAX_KEY_NODES_PER_AUDIT
    ]
    report.audited_events = [ev["event_key"] for ev in key_nodes]

    for ev in key_nodes:
        key = str(ev["event_key"])
        paths = [eff.get("path") for eff in ev.get("hard_effects") or [] if eff.get("path")]
        counterfactual = _replay(base_states, events, key)
        changed = _state_diff(actual, counterfactual, paths)

        # downstream targets this event supports
        targets = sorted(
            {
                e["to"]
                for e in out_edges.get(key, [])
                if e["relation"] in SUPPORT_RELATIONS
            }
        )
        for target in targets:
            remaining = [
                e["from"]
                for e in in_edges.get(target, [])
                if e["from"] != key and e["relation"] in SUPPORT_RELATIONS
            ]
            if not remaining:
                classification = "necessary_support"
                support = "none"
            else:
                classification = "contributing_support"
                support = "remaining"

            report.findings.append(
                CounterfactualFinding(
                    removed_event_key=key,
                    checked_target_key=target,
                    support_after_removal=support,
                    classification=classification,
                    remaining_support_keys=remaining[:10],
                    detail=(
                        f"移除 {key} 后 {len(changed)} 条状态路径改变；"
                        f"剩余支持 {len(remaining)} 条"
                    ),
                )
            )

        # §33.2 redundancy: removal changes nothing but event is motive-emphasized
        if not changed:
            motive_edges = [
                e for e in out_edges.get(key, []) if e["relation"] in MOTIVE_RELATIONS
            ]
            hard_declared = any(
                e.get("mode") == "hard" for e in out_edges.get(key, [])
            )
            if motive_edges or hard_declared:
                report.findings.append(
                    CounterfactualFinding(
                        removed_event_key=key,
                        checked_target_key=key,
                        support_after_removal="sufficient",
                        classification=(
                            "motivation_redundancy" if motive_edges else "false_causal_emphasis"
                        ),
                        remaining_support_keys=[],
                        detail=(
                            "移除该事件后重放状态完全不变，但契约将其标记为"
                            + ("核心动机边" if motive_edges else "硬因果边")
                        ),
                    )
                )

    report.ok = True
    return report


def _as_provisional(ev: dict) -> ProvisionalEvent:
    try:
        return ProvisionalEvent.model_validate(ev)
    except Exception:
        return ProvisionalEvent(event_key=str(ev.get("event_key") or "evt"), action="unknown")


async def audit_chapter_counterfactual(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
) -> dict[str, Any]:
    """DB entry: load persisted contracts + latest L4, run the pure audit."""
    from app.models import MemoryL4StateSnapshot, SceneReasoningContract

    contract_rows = (
        await db.execute(
            select(SceneReasoningContract).where(
                SceneReasoningContract.chapter_id == chapter_id,
                SceneReasoningContract.book_id == book_id,
            )
        )
    ).scalars().all()
    contracts = [r.contract_json or {} for r in contract_rows]
    contracts = [c for c in contracts if isinstance(c, dict)]

    l4_rows = (
        await db.execute(
            select(MemoryL4StateSnapshot)
            .where(MemoryL4StateSnapshot.book_id == book_id)
            .order_by(MemoryL4StateSnapshot.as_of_chapter.desc())
        )
    ).scalars().all()
    states_by_char: dict[str, dict[str, Any]] = {}
    for row in l4_rows:
        cid = str(row.entity_id)
        if cid not in states_by_char and isinstance(row.state, dict):
            states_by_char[cid] = row.state

    report = audit_counterfactual(contracts, states_by_char)
    return report.model_dump(mode="json")
