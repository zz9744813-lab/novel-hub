"""v9.0 pipeline stage: compile planner proposals into Scene Contracts (spec §23.3).

Flow per chapter:
    LLM Proposal → Causal Simulation → Invalid Proposal Reject/Repair → Valid Scene Contract

- Compiles each scene proposal sequentially; state flows from scene to scene
  via hard effects so later preconditions see earlier outcomes.
- Validates every contract with the CausalEngine; blockers are surfaced,
  advisories are logged. A scene with NO causal proposal fields at all still
  gets a minimal contract (goal + word target) so drafting is never blocked
  by an empty causal section — degradation, not failure.
- Persists SceneReasoningContract rows (status=proposed). No LLM calls here.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.contracts.narrative import (
    ContractValidationReport,
    SceneContract,
    SceneProposal,
)
from app.database import async_session_factory
from app.engine.cognitive_config import DEFAULT_CAUSAL_CONFIG
from app.engine.narrative_state import apply_state_deltas, normalize_state
from app.engine.scene_contract import SceneContractCompiler
from app.models import CharacterCoreAnchor, MemoryL4StateSnapshot, SceneReasoningContract

logger = logging.getLogger("novelforge.causal_compile")

MAX_VALIDATION_FINDINGS_KEPT = 40


async def load_states_and_anchors(
    book_id: uuid.UUID,
    character_ids: list[str],
    as_of_chapter: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """L4 states + active Core Anchors keyed by character id string."""
    async with async_session_factory() as db:
        states: dict[str, dict[str, Any]] = {}
        for cid in character_ids[:20]:
            try:
                entity_id = uuid.UUID(cid) if isinstance(cid, str) else cid
            except (ValueError, TypeError):
                continue
            snap = (
                await db.execute(
                    select(MemoryL4StateSnapshot)
                    .where(
                        MemoryL4StateSnapshot.book_id == book_id,
                        MemoryL4StateSnapshot.entity_id == entity_id,
                        MemoryL4StateSnapshot.as_of_chapter <= max(as_of_chapter - 1, 0),
                    )
                    .order_by(
                        MemoryL4StateSnapshot.as_of_chapter.desc(),
                        MemoryL4StateSnapshot.version.desc(),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if snap:
                states[str(cid)] = snap.state or {}

        anchor_rows = (
            await db.execute(
                select(CharacterCoreAnchor).where(
                    CharacterCoreAnchor.book_id == book_id,
                    CharacterCoreAnchor.status == "active",
                )
            )
        ).scalars().all()
    anchors_by_char: dict[str, list[dict[str, Any]]] = {}
    for r in anchor_rows:
        anchors_by_char.setdefault(str(r.character_id), []).append(
            {
                "anchor_code": r.anchor_code,
                "anchor_type": r.anchor_type,
                "statement": r.statement,
                "priority": r.priority,
                "rigidity": r.rigidity,
                "is_locked": r.is_locked,
            }
        )
    return states, anchors_by_char


async def compile_chapter_contracts(
    *,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    chapter_no: int,
    scene_plan: dict,
    l4_states: dict[str, dict[str, Any]] | None = None,
    core_anchors_by_char: dict[str, list[dict[str, Any]]] | None = None,
    outline_expected_effects: list[dict[str, Any]] | None = None,
    source_run_id: uuid.UUID | None = None,
    persist: bool = True,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile + validate + persist per-scene contracts.

    Returns:
        {
          "contracts": [contract dict, ...]   # aligned with scene_plan.scenes order
          "reports":  [validation dict, ...]
          "blockers": [finding dict, ...]     # empty when all scenes are valid
          "compiled_count": int,
        }
    """
    cfg = {**DEFAULT_CAUSAL_CONFIG, **(config or {})}
    compiler = SceneContractCompiler(cfg)

    states = l4_states or {}
    anchors = core_anchors_by_char or {}

    scenes_raw = (scene_plan or {}).get("scenes") or []
    contracts: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []

    # Mutable working state: hard effects of scene N feed scene N+1
    working: dict[str, dict[str, Any]] = {k: normalize_state(v) for k, v in states.items()}

    for idx, raw in enumerate(scenes_raw, start=1):
        if not isinstance(raw, dict):
            continue
        try:
            proposal = SceneProposal.model_validate(raw)
        except Exception as e:
            logger.warning("scene %s proposal invalid (%s); compiling minimal contract", idx, e)
            proposal = SceneProposal(scene_no=idx, goal=raw.get("goal") or "推进场景目标")

        contract = compiler.compile_scene_contract(
            proposal,
            chapter_no=chapter_no,
            scene_no=int(proposal.scene_no or idx),
            states_by_char=working,
            core_anchors_by_char=anchors,
            outline_expected_effects=outline_expected_effects,
        )

        anchor_ids_by_char = {
            cid: {a["anchor_code"] for a in lst if a.get("anchor_code")}
            for cid, lst in anchors.items()
        }
        report = compiler.validate_scene_contract(contract, working, anchor_ids_by_char)
        reports.append(
            {
                "scene_no": contract.scene_no,
                "ok": report.ok,
                "findings": [f.model_dump(mode="json") for f in report.findings][
                    :MAX_VALIDATION_FINDINGS_KEPT
                ],
            }
        )
        blockers.extend(
            f.model_dump(mode="json") for f in report.findings if f.severity == "blocker"
        )

        contracts.append(contract.model_dump(mode="json", by_alias=True))

        # advance working state by this scene's hard effects (per character slice)
        for eff in contract.expected_effects:
            if eff.mode != "hard":
                continue
            char_head = eff.path.split(".")[0]
            if char_head in working and "." in eff.path:
                payload = eff.model_dump()
                payload["path"] = eff.path.split(".", 1)[1]
                rel = StateDelta.model_validate(payload)
                working[char_head] = apply_state_deltas(working[char_head], [rel])
            elif char_head in working:
                continue  # path IS the character id itself — nothing to set
            else:
                # global path (e.g. world.foo) — apply to every state
                for k in list(working.keys()):
                    working[k] = apply_state_deltas(working[k], [eff])

    if persist and contracts:
        await _persist_contracts(
            book_id=book_id,
            chapter_id=chapter_id,
            chapter_no=chapter_no,
            contracts=contracts,
            reports=reports,
            source_run_id=source_run_id,
        )

    return {
        "contracts": contracts,
        "reports": reports,
        "blockers": blockers,
        "compiled_count": len(contracts),
    }


async def _persist_contracts(
    *,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    chapter_no: int,
    contracts: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    source_run_id: uuid.UUID | None,
) -> None:
    report_by_scene = {r.get("scene_no"): r for r in reports}
    async with async_session_factory() as db:
        for c in contracts:
            scene_no = int(c.get("scene_no") or 0)
            contract_hash = str(c.get("contract_hash") or "")
            if not contract_hash:
                continue
            existing = (
                await db.execute(
                    select(SceneReasoningContract).where(
                        SceneReasoningContract.chapter_id == chapter_id,
                        SceneReasoningContract.scene_no == scene_no,
                        SceneReasoningContract.contract_hash == contract_hash,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                continue
            report = report_by_scene.get(scene_no) or {}
            db.add(
                SceneReasoningContract(
                    book_id=book_id,
                    chapter_id=chapter_id,
                    scene_no=scene_no,
                    contract_json=c,
                    contract_hash=contract_hash,
                    source_run_id=source_run_id,
                    status="proposed",
                    validation_json={
                        "ok": report.get("ok", True),
                        "findings": report.get("findings", []),
                        "chapter_no": chapter_no,
                    },
                )
            )
        await db.commit()


async def load_chapter_contracts(
    chapter_id: uuid.UUID,
    *,
    statuses: tuple[str, ...] = ("proposed", "validated", "realized", "finalized"),
) -> list[dict[str, Any]]:
    """Latest contract per scene for a chapter (newest first wins)."""
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(SceneReasoningContract)
                .where(
                    SceneReasoningContract.chapter_id == chapter_id,
                    SceneReasoningContract.status.in_(list(statuses)),
                )
                .order_by(SceneReasoningContract.created_at.desc())
            )
        ).scalars().all()
    by_scene: dict[int, dict[str, Any]] = {}
    for r in rows:
        sn = int(r.scene_no)
        if sn not in by_scene:
            by_scene[sn] = {
                "id": str(r.id),
                "scene_no": sn,
                "status": r.status,
                "contract_hash": r.contract_hash,
                "validation": r.validation_json or {},
                **(r.contract_json or {}),
            }
    return [by_scene[k] for k in sorted(by_scene)]


async def mark_contract_status(
    contract_id: uuid.UUID | str,
    status: str,
    *,
    validation_patch: dict[str, Any] | None = None,
) -> bool:
    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(SceneReasoningContract).where(
                    SceneReasoningContract.id == uuid.UUID(str(contract_id))
                )
            )
        ).scalar_one_or_none()
        if not row:
            return False
        row.status = status
        if validation_patch:
            merged = dict(row.validation_json or {})
            merged.update(validation_patch)
            row.validation_json = merged
        await db.commit()
        return True


def summarize_reports(reports: list[dict[str, Any]]) -> dict[str, int]:
    """Roll up validation reports for observability."""
    total = 0
    by_code: dict[str, int] = {}
    for r in reports:
        for f in r.get("findings") or []:
            code = str(f.get("code") or "unknown")
            by_code[code] = by_code.get(code, 0) + 1
            total += 1
    return {"total_findings": total, "by_code": by_code}
