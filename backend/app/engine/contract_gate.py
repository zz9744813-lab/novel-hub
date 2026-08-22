"""v9.1 Pre-Draft Contract Gate (spec §7.1).

Position: SceneContractCompiler → ContractGate → Draft

Deterministic and fail-closed. Aggregates the per-scene validation reports
produced during compilation plus independent structural checks across ALL
compiled contracts of the chapter. Any blocker means drafting must not start
— the chapter goes NEEDS_HUMAN instead of silently degrading.

Blocker codes (spec §7.1):
    CAUSAL_PRECONDITION_FAILED
    HARD_EFFECT_CONFLICT
    PIVOTAL_INTENTION_UNRESOLVED
    BELIEF_SOURCE_MISSING
    KNOWLEDGE_BOUNDARY_MISSING
    INVALID_STATE_PATH

No LLM calls here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.contracts.narrative import SceneContract
from app.engine.narrative_state import validate_state_path

logger = logging.getLogger("novelforge.contract_gate")

GATE_VERSION = "v9.1"

PRE_DRAFT_BLOCKER_CODES = {
    "CAUSAL_PRECONDITION_FAILED",
    "HARD_EFFECT_CONFLICT",
    "PIVOTAL_INTENTION_UNRESOLVED",
    "BELIEF_SOURCE_MISSING",
    "KNOWLEDGE_BOUNDARY_MISSING",
    "INVALID_STATE_PATH",
}


@dataclass
class ContractGateResult:
    ok: bool
    blockers: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    contracts_checked: int = 0

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "gate_version": GATE_VERSION,
            "contracts_checked": self.contracts_checked,
            "blockers": self.blockers,
            "warnings": self.warnings,
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


def _path_ok(path: str) -> bool:
    """Legal state path: either a bare path, or '{entity_id}.bare.path' where
    the head is an entity id (uuids/hyphenated ids are legal heads)."""
    if validate_state_path(path):
        return True
    head, sep, rest = path.partition(".")
    return (
        bool(sep)
        and bool(head.strip())
        and " " not in head
        and validate_state_path(rest)
    )


def _finding(code: str, scene_no: int, message: str, **extra) -> dict:
    f = {"code": code, "severity": "blocker", "scene_no": scene_no, "message": message}
    f.update(extra)
    return f


def _check_contract(
    contract: SceneContract, findings: list[dict], warnings: list[dict]
) -> None:
    sn = contract.scene_no
    event_keys = {e.event_key for e in contract.provisional_events}

    # INVALID_STATE_PATH — every structured path must be legal
    for eff in contract.expected_effects:
        if not _path_ok(eff.path):
            findings.append(
                _finding("INVALID_STATE_PATH", sn, f"expected_effect path illegal: {eff.path}", path=eff.path)
            )
    for pred in contract.preconditions:
        if not _path_ok(pred.path):
            findings.append(
                _finding("INVALID_STATE_PATH", sn, f"precondition path illegal: {pred.path}", path=pred.path)
            )

    # BELIEF_SOURCE_MISSING — every belief delta needs a real source event
    perceived_by_char: dict[str, set[str]] = {}
    for p in contract.perceptions:
        perceived_by_char.setdefault(str(p.character_id), set()).add(str(p.event_key))
    for b in contract.belief_deltas:
        sources = [str(k) for k in (b.source_event_keys or [])]
        if not sources:
            findings.append(
                _finding(
                    "BELIEF_SOURCE_MISSING", sn,
                    f"belief '{b.belief_key}' of {b.character_id} has no source event",
                    character_id=b.character_id, belief_key=b.belief_key,
                )
            )
            continue
        missing = [k for k in sources if k not in event_keys]
        if missing:
            findings.append(
                _finding(
                    "BELIEF_SOURCE_MISSING", sn,
                    f"belief '{b.belief_key}' sources unknown events: {missing}",
                    character_id=b.character_id, belief_key=b.belief_key,
                )
            )
        # KNOWLEDGE_BOUNDARY_MISSING — the owner must perceive a source event
        seen = perceived_by_char.get(str(b.character_id), set())
        if not (seen & set(sources)):
            findings.append(
                _finding(
                    "KNOWLEDGE_BOUNDARY_MISSING", sn,
                    f"{b.character_id} changes belief '{b.belief_key}' without perceiving any source event",
                    character_id=b.character_id, belief_key=b.belief_key,
                )
            )

    # PIVOTAL_INTENTION_UNRESOLVED — pivotal intents must carry attribution
    for it in contract.intentions:
        if it.weight == "pivotal" and it.attribution_status != "supported":
            findings.append(
                _finding(
                    "PIVOTAL_INTENTION_UNRESOLVED", sn,
                    f"pivotal intention of {it.character_id} unresolved: {it.action_intent[:60]}",
                    character_id=it.character_id,
                    reason=it.unresolved_reason,
                )
            )
        elif it.attribution_status != "supported":
            warnings.append(
                {
                    "code": "INTENTION_UNRESOLVED",
                    "severity": "minor",
                    "scene_no": sn,
                    "message": f"non-pivotal intention of {it.character_id} unresolved",
                    "character_id": it.character_id,
                }
            )

    # HARD_EFFECT_CONFLICT — two hard effects on one path with different values
    hard_by_path: dict[str, list[Any]] = {}
    for eff in contract.expected_effects:
        if eff.mode == "hard":
            hard_by_path.setdefault(eff.path, []).append(eff)
    for path, effs in hard_by_path.items():
        values = [e.value for e in effs]
        if len(values) > 1 and any(v != values[0] for v in values[1:]):
            findings.append(
                _finding(
                    "HARD_EFFECT_CONFLICT", sn,
                    f"multiple hard effects on '{path}' with different values: {values}",
                    path=path,
                )
            )


def run_contract_gate(
    contracts: list[Any],
    reports: list[dict] | None = None,
) -> ContractGateResult:
    """Gate all compiled scene contracts of one chapter before drafting."""
    blockers: list[dict] = []
    warnings: list[dict] = []
    checked = 0

    # 1) aggregate compile-time validation findings with blocker codes
    for rep in reports or []:
        for f in (rep.get("findings") or []) if isinstance(rep, dict) else []:
            code = str(f.get("code") or "")
            if code in PRE_DRAFT_BLOCKER_CODES and f.get("severity") != "minor":
                blockers.append(
                    {
                        "code": code,
                        "severity": "blocker",
                        "scene_no": f.get("scene_no") or rep.get("scene_no"),
                        "message": f.get("detail") or f.get("message") or code,
                        "source": "compile_report",
                    }
                )

    # 2) independent structural checks per contract
    for raw in contracts or []:
        contract = _coerce(raw)
        if contract is None:
            blockers.append(
                {
                    "code": "CONTRACT_UNPARSEABLE",
                    "severity": "blocker",
                    "scene_no": (raw or {}).get("scene_no") if isinstance(raw, dict) else None,
                    "message": "scene contract failed structural parse at gate",
                    "source": "contract_gate",
                }
            )
            continue
        checked += 1
        _check_contract(contract, blockers, warnings)

    result = ContractGateResult(
        ok=not blockers,
        blockers=blockers,
        warnings=warnings,
        contracts_checked=checked,
    )
    if blockers:
        logger.error(
            "contract gate BLOCKED: %s blocker(s), first=%s",
            len(blockers), blockers[0].get("code"),
        )
    return result
