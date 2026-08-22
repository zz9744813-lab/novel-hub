"""v9.1 Post-Draft Realization Gate (spec §7.2).

Position: Draft → Review/Patch → StateExtractor → RealizationGate → Finalizer

Verifies that the ACTUAL drafted chapter realizes what the compiled scene
contracts required — structured data only, no prose guessing, no LLM:

1. Hard Effect 是否实现                 → HARD_EFFECT_NOT_REALIZED (blocker)
2. 是否出现相反状态                     → HARD_EFFECT_CONTRADICTED (blocker)
3. Knowledge Boundary 是否越界          → ILLEGAL_KNOWLEDGE (blocker)
4. Pivotal action 是否有合法归因        → PIVOTAL_ATTRIBUTION_UNRESOLVED (blocker)
5. Belief change 是否有合法 cause event → BELIEF_CHANGE_WITHOUT_EVIDENCE (major)
6. Hard causal edge 映射到实际事件      → HARD_EDGE_UNMAPPED (major)
7. unresolved attribution 被当作 canon  → UNRESOLVED_ATTRIBUTION_AS_CANON (major)

Gate fails closed: blocker OR major findings mean the chapter is NOT finalized.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.contracts.narrative import SceneContract

logger = logging.getLogger("novelforge.realization_gate")

GATE_VERSION = "v9.1"

BLOCKER_CODES = {
    "HARD_EFFECT_NOT_REALIZED",
    "HARD_EFFECT_CONTRADICTED",
    "ILLEGAL_KNOWLEDGE",
    "PIVOTAL_ATTRIBUTION_UNRESOLVED",
}


@dataclass
class RealizationGateResult:
    ok: bool
    findings: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "gate_version": GATE_VERSION,
            "findings": self.findings,
            "summary": self.summary,
        }


def _coerce(raw: Any) -> SceneContract | None:
    if isinstance(raw, SceneContract):
        return raw
    if isinstance(raw, dict):
        try:
            return SceneContract.model_validate(raw)
        except Exception:
            return None
    return None


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _suffix(path: str, segments: int = 2) -> str:
    parts = path.split(".")
    return ".".join(parts[-segments:]) if len(parts) > segments else path


@dataclass
class _Observation:
    char_id: str | None
    path: str
    value: Any
    scene_no: int | None = None


def _observations_from_events(actual_events: list[dict]) -> list[_Observation]:
    out: list[_Observation] = []
    for ev in actual_events or []:
        if not isinstance(ev, dict):
            continue
        path = str(ev.get("field") or "").strip()
        if not path:
            continue
        char = str(ev.get("entity_id")) if ev.get("entity_id") is not None else None
        out.append(
            _Observation(
                char_id=char,
                path=path,
                value=ev.get("new_value"),
                scene_no=ev.get("scene_no"),
            )
        )
    return out


def _observations_from_deltas(deltas: list[dict]) -> list[_Observation]:
    out: list[_Observation] = []
    for d in deltas or []:
        if not isinstance(d, dict):
            continue
        path = str(d.get("path") or "").strip()
        if not path:
            continue
        head = path.split(".")[0]
        rest = path.split(".", 1)[1] if "." in path else path
        out.append(_Observation(char_id=head, path=rest, value=d.get("value")))
    return out


def _match(effect_path: str, obs: _Observation) -> bool:
    """effect_path may be 'char.a.b' (head = character) or bare 'a.b'."""
    head = effect_path.split(".")[0]
    rest = effect_path.split(".", 1)[1] if "." in effect_path else effect_path
    if obs.char_id is not None and obs.char_id == head:
        return obs.path == rest or _suffix(obs.path) == _suffix(rest)
    # global path or unknown head: match on the relative/suffix form
    return obs.path in (effect_path, rest) or _suffix(obs.path) == _suffix(rest)


def run_realization_gate(
    *,
    scene_contracts: list[Any],
    actual_events: list[dict],
    actual_state_deltas: list[dict] | None = None,
    reaction_evidence: list[dict] | None = None,
    attributions: list[dict] | None = None,
) -> RealizationGateResult:
    """Verify actual extracted events realize the compiled contracts."""
    findings: list[dict] = []
    contracts = [c for c in (_coerce(raw) for raw in scene_contracts or []) if c]
    observations = _observations_from_events(actual_events) + _observations_from_deltas(
        actual_state_deltas or []
    )
    reactions = [r for r in (reaction_evidence or []) if isinstance(r, dict)]
    attrs = [a for a in (attributions or []) if isinstance(a, dict)]

    actual_event_keys = {
        str(e.get("event_key")) for e in actual_events or [] if isinstance(e, dict) and e.get("event_key")
    }
    realized_prov_keys = {
        str(e.get("realized_provisional_event_key"))
        for e in actual_events or []
        if isinstance(e, dict) and e.get("realized_provisional_event_key")
    }
    prov_keys = set()
    for c in contracts:
        prov_keys.update(e.event_key for e in c.provisional_events)

    hard_effects_total = 0
    hard_effects_realized = 0

    # ── checks 1/2: hard effect realization + contradiction ──────────
    for c in contracts:
        for eff in c.expected_effects:
            if eff.mode != "hard":
                continue
            hard_effects_total += 1
            matched = [o for o in observations if _match(eff.path, o)]
            if not matched:
                findings.append(
                    {
                        "code": "HARD_EFFECT_NOT_REALIZED",
                        "severity": "blocker",
                        "scene_no": c.scene_no,
                        "message": f"hard effect on '{eff.path}' not realized by any extracted event",
                        "path": eff.path,
                        "expected_value": eff.value,
                    }
                )
                continue
            hard_effects_realized += 1
            for o in matched:
                if _is_num(eff.value) and _is_num(o.value):
                    if float(eff.value) * float(o.value) < 0:
                        findings.append(
                            {
                                "code": "HARD_EFFECT_CONTRADICTED",
                                "severity": "blocker",
                                "scene_no": c.scene_no,
                                "message": (
                                    f"opposite state on '{eff.path}': "
                                    f"expected {eff.value}, actual {o.value}"
                                ),
                                "path": eff.path,
                                "expected_value": eff.value,
                                "actual_value": o.value,
                            }
                        )
                elif o.value != eff.value:
                    findings.append(
                        {
                            "code": "HARD_EFFECT_VALUE_MISMATCH",
                            "severity": "minor",
                            "scene_no": c.scene_no,
                            "message": f"value mismatch on '{eff.path}': expected {eff.value}, actual {o.value}",
                            "path": eff.path,
                        }
                    )

    # ── check 3: knowledge boundary — attributions may only cite  ────
    # events that actually exist (actual or contracted provisional)
    legal_cause_keys = actual_event_keys | prov_keys
    attr_by_reaction: dict[str, dict] = {}
    for a in attrs:
        if a.get("reaction_key"):
            attr_by_reaction.setdefault(str(a["reaction_key"]), a)
        unknown = [
            str(k) for k in (a.get("cause_event_keys") or []) if str(k) not in legal_cause_keys
        ]
        if unknown:
            findings.append(
                {
                    "code": "ILLEGAL_KNOWLEDGE",
                    "severity": "blocker",
                    "scene_no": a.get("scene_no"),
                    "message": (
                        f"attribution for reaction '{a.get('reaction_key')}' cites "
                        f"unknown events: {unknown[:5]}"
                    ),
                    "reaction_key": a.get("reaction_key"),
                }
            )
        # supported attributions must actually carry support (spec §29.2)
        has_support = bool(
            (a.get("core_anchor_ids") or [])
            or (a.get("belief_keys") or [])
            or (a.get("goal_keys") or [])
            or (a.get("relationship_refs") or [])
            or (a.get("cause_event_keys") or [])
        )
        if a.get("status") == "supported" and not has_support:
            findings.append(
                {
                    "code": "ATTRIBUTION_STATUS_INVALID",
                    "severity": "major",
                    "scene_no": a.get("scene_no"),
                    "message": f"attribution '{a.get('reaction_key')}' marked supported without any support",
                    "reaction_key": a.get("reaction_key"),
                }
            )

    # ── check 4: pivotal actions need a supported attribution ────────
    pivotal_total = 0
    pivotal_supported = 0
    for r in reactions:
        if r.get("weight") != "pivotal":
            continue
        pivotal_total += 1
        a = attr_by_reaction.get(str(r.get("reaction_key")))
        if a and a.get("status") == "supported":
            pivotal_supported += 1
        else:
            findings.append(
                {
                    "code": "PIVOTAL_ATTRIBUTION_UNRESOLVED",
                    "severity": "blocker",
                    "scene_no": r.get("scene_no"),
                    "message": (
                        f"pivotal reaction '{r.get('reaction_key')}' lacks a "
                        "supported attribution"
                    ),
                    "reaction_key": r.get("reaction_key"),
                }
            )

    # ── check 5: belief changes need evidence ────────────────────────
    for ev in actual_events or []:
        if not isinstance(ev, dict):
            continue
        path = str(ev.get("field") or "")
        if "beliefs" not in path:
            continue
        if not ev.get("evidence_paragraph_key"):
            findings.append(
                {
                    "code": "BELIEF_CHANGE_WITHOUT_EVIDENCE",
                    "severity": "major",
                    "scene_no": ev.get("scene_no"),
                    "message": (
                        f"belief change '{path}' of {ev.get('entity_id')} "
                        "has no evidence paragraph"
                    ),
                    "path": path,
                }
            )

    # ── check 6: hard causal edges must map to actual events ─────────
    hard_edges_total = 0
    hard_edges_mapped = 0
    for c in contracts:
        for edge in c.causal_edges:
            if edge.mode != "hard":
                continue
            hard_edges_total += 1
            if edge.from_key in realized_prov_keys and edge.to_key in realized_prov_keys:
                hard_edges_mapped += 1
            elif realized_prov_keys:
                findings.append(
                    {
                        "code": "HARD_EDGE_UNMAPPED",
                        "severity": "major",
                        "scene_no": c.scene_no,
                        "message": (
                            f"hard edge {edge.from_key} → {edge.to_key} endpoints "
                            "not both realized by extracted events"
                        ),
                        "from_key": edge.from_key,
                        "to_key": edge.to_key,
                    }
                )
    if hard_edges_total and not realized_prov_keys:
        findings.append(
            {
                "code": "HARD_EDGE_MAPPING_MISSING",
                "severity": "major",
                "scene_no": None,
                "message": (
                    f"{hard_edges_total} hard causal edges exist but no extracted "
                    "event carries realized_provisional_event_key"
                ),
            }
        )

    # ── check 7: unresolved attribution used as canon cause ──────────
    belief_event_keys = {
        str(e.get("event_key"))
        for e in actual_events or []
        if isinstance(e, dict) and "beliefs" in str(e.get("field") or "")
    }
    for a in attrs:
        if a.get("status") != "unresolved":
            continue
        cited_belief_causes = [
            str(k) for k in (a.get("cause_event_keys") or []) if str(k) in belief_event_keys
        ]
        if cited_belief_causes:
            findings.append(
                {
                    "code": "UNRESOLVED_ATTRIBUTION_AS_CANON",
                    "severity": "major",
                    "scene_no": a.get("scene_no"),
                    "message": (
                        f"unresolved attribution '{a.get('reaction_key')}' cited as "
                        f"cause of belief change(s) {cited_belief_causes[:3]}"
                    ),
                    "reaction_key": a.get("reaction_key"),
                }
            )

    hard = [f for f in findings if f.get("severity") in ("blocker", "major")]
    summary = {
        "contracts": len(contracts),
        "actual_events": len(actual_events or []),
        "hard_effects_total": hard_effects_total,
        "hard_effects_realized": hard_effects_realized,
        "hard_edges_total": hard_edges_total,
        "hard_edges_mapped": hard_edges_mapped,
        "pivotal_reactions": pivotal_total,
        "pivotal_supported": pivotal_supported,
        "attributions": len(attrs),
        "reactions": len(reactions),
    }
    result = RealizationGateResult(ok=not hard, findings=findings, summary=summary)
    if hard:
        logger.error(
            "realization gate BLOCKED: %s hard finding(s), first=%s",
            len(hard), hard[0].get("code"),
        )
    else:
        logger.info("realization gate passed: %s", summary)
    return result
