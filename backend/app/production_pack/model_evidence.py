"""Explicit production bootstrap for configured model evidence.

The deployment controller invokes this command before switching releases.  It
is the operator-approved write path that creates missing env-backed bindings,
performs a tiny text handshake for those configured models, and runs (or
content-addressedly reuses) ability/context evidence.  Ordinary writing-session
preflight remains health-only and never launches an expensive benchmark.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from app.agents.registry import required_roles
from app.model_autopilot.capability import (
    CONTEXT_REQUIRED_ROLES,
    required_context_for,
)
from app.model_autopilot.preflight import (
    ROLE_CONTEXT_ESTIMATE,
    bootstrap_catalog_and_probes,
)
from app.models import ModelCatalog, ModelEvalRun, ModelHealthSnapshot
from app.v74_utils import ModelBindingService

from .contracts import ProductionPack
from .service import stable_id


KNOWN_CONFIGURED_MODEL_ALIASES = {
    ("new-api", "deepseek-v4-flash-free"): ("new-api", "deepseek-v4-flash"),
    ("new-api", "z-ai/glm-5.2"): ("new-api", "glm-5.2"),
    ("openrouter", "z-ai/glm-5.2"): ("new-api", "glm-5.2"),
}


async def _effective_targets(db, book_id: uuid.UUID) -> tuple[dict, list[str]]:
    service = ModelBindingService(db)
    targets: dict[tuple[str, str], set[str]] = defaultdict(set)
    missing: list[str] = []
    for role in required_roles():
        binding = await service.get_binding(role, book_id)
        if binding is None:
            missing.append(role)
            continue
        targets[(binding.provider, binding.primary_model)].add(role)
    return dict(targets), missing


def _required_context(roles: set[str]) -> int:
    required = 0
    for role in roles & CONTEXT_REQUIRED_ROLES:
        estimated_input, reserved_output = ROLE_CONTEXT_ESTIMATE[role]
        required = max(
            required,
            required_context_for(estimated_input, reserved_output, 0),
        )
    return required


def _choose_role_assignments(
    evaluations: dict[tuple[str, str], dict],
    current_targets: dict[tuple[str, str], set[str]],
) -> tuple[dict[str, tuple[str, str]], list[dict]]:
    """Keep a qualified current target, otherwise choose the best replacement.

    Ability and context calls happen before this function.  Selection is pure
    and deterministic.  A qualified current assignment is preserved so a
    small synthetic-score difference cannot churn an intentional production
    choice.  When it fails, highest benchmark score wins and provider/model
    identity breaks ties.  Missing/stale or context-insufficient evidence is
    never a candidate.
    """

    current_by_role = {
        role: target
        for target, roles in current_targets.items()
        for role in roles
    }
    assignments: dict[str, tuple[str, str]] = {}
    unresolved: list[dict] = []
    for role in sorted(current_by_role):
        required_context = _required_context({role})
        candidates: list[tuple[float, bool, str, str]] = []
        for (provider, model_id), evaluation in evaluations.items():
            state = evaluation.get("state") or {}
            detail = (state.get("role_evidence") or {}).get(role) or {}
            effective_context = (state.get("context_profile") or {}).get("effective")
            if detail.get("state") != "valid" or not detail.get("passed"):
                continue
            if required_context and (
                effective_context is None or effective_context < required_context
            ):
                continue
            score = detail.get("score")
            if score is None:
                continue
            candidates.append(
                (
                    float(score),
                    (provider, model_id) == current_by_role[role],
                    provider,
                    model_id,
                )
            )
        if not candidates:
            unresolved.append(
                {
                    "role": role,
                    "current_provider": current_by_role[role][0],
                    "current_model": current_by_role[role][1],
                    "required_context": required_context or None,
                    "reason": "no_qualified_evaluated_model",
                }
            )
            continue
        current_candidate = next((item for item in candidates if item[1]), None)
        candidates.sort(key=lambda item: (-item[0], item[2], item[3]))
        _, _, provider, model_id = current_candidate or candidates[0]
        assignments[role] = (provider, model_id)
    return assignments, unresolved


async def _apply_role_assignments(
    db,
    book_id: uuid.UUID,
    assignments: dict[str, tuple[str, str]],
) -> list[dict]:
    if not assignments:
        return []
    service = ModelBindingService(db)
    changed: list[dict] = []
    catalogs = list(
        (
            await db.execute(
                select(ModelCatalog).where(
                    ModelCatalog.enabled.is_(True),
                    ModelCatalog.availability_status == "available",
                )
            )
        )
        .scalars()
        .all()
    )
    catalog_ids = {
        (catalog.provider, catalog.model_id): str(catalog.id) for catalog in catalogs
    }
    for role, (provider, model_id) in sorted(assignments.items()):
        binding = await service.get_binding(role, book_id)
        if binding is None:
            continue
        current = (binding.provider, binding.primary_model)
        selected_catalog_id = catalog_ids.get((provider, model_id))
        allowed_before = list(binding.allowed_model_ids or [])
        blocked_before = list(binding.blocked_model_ids or [])
        allowed_after = list(allowed_before)
        blocked_after = list(blocked_before)
        if selected_catalog_id and allowed_after and selected_catalog_id not in allowed_after:
            allowed_after.append(selected_catalog_id)
        if selected_catalog_id and selected_catalog_id in blocked_after:
            blocked_after = [item for item in blocked_after if item != selected_catalog_id]

        model_changed = current != (provider, model_id)
        constraints_changed = (
            allowed_after != allowed_before or blocked_after != blocked_before
        )
        if not model_changed and not constraints_changed:
            continue
        reason = (
            "replace unqualified production model using reusable evidence"
            if model_changed
            else "reconcile selected production model with routing constraints"
        )
        binding = await service.update_binding(
            binding.id,
            new_provider=provider,
            new_model=model_id,
            reason=reason,
            changed_by="production_release",
        )
        binding.allowed_model_ids = allowed_after
        binding.blocked_model_ids = blocked_after
        binding.updated_by = "production_release"
        binding.updated_at = datetime.now(timezone.utc)
        changed.append(
            {
                "role": role,
                "from": {"provider": current[0], "model": current[1]},
                "to": {"provider": provider, "model": model_id},
                "constraints": {
                    "selected_catalog_id": selected_catalog_id,
                    "added_to_allowed": bool(
                        selected_catalog_id
                        and selected_catalog_id not in allowed_before
                        and selected_catalog_id in allowed_after
                    ),
                    "removed_from_blocked": bool(
                        selected_catalog_id and selected_catalog_id in blocked_before
                    ),
                },
            }
        )
    return changed


async def _reconcile_known_model_aliases(db, book_id: uuid.UUID) -> dict:
    """Replace only exact, unavailable legacy IDs with discovered equivalents.

    This runs solely inside the explicit production release command.  It never
    guesses a model: both the legacy tuple and replacement are allow-listed,
    and the replacement must be present in the live provider catalog.
    """

    catalogs = list(
        (
            await db.execute(
                select(ModelCatalog).where(
                    ModelCatalog.enabled.is_(True),
                    ModelCatalog.availability_status == "available",
                )
            )
        ).scalars().all()
    )
    available = {(catalog.provider, catalog.model_id) for catalog in catalogs}
    service = ModelBindingService(db)
    changed: list[dict] = []
    unresolved: list[dict] = []
    seen_bindings: set[uuid.UUID] = set()
    for role in required_roles():
        binding = await service.get_binding(role, book_id)
        if binding is None or binding.id in seen_bindings:
            continue
        seen_bindings.add(binding.id)
        current = (binding.provider, binding.primary_model)
        target = KNOWN_CONFIGURED_MODEL_ALIASES.get(current)
        if target is None or current in available:
            continue
        if target not in available:
            unresolved.append(
                {
                    "role": role,
                    "provider": current[0],
                    "model": current[1],
                    "target_provider": target[0],
                    "target_model": target[1],
                }
            )
            continue
        await service.update_binding(
            binding.id,
            new_provider=target[0],
            new_model=target[1],
            reason="replace unavailable legacy model alias during production release",
            changed_by="production_release",
        )
        changed.append(
            {
                "role": role,
                "from": {"provider": current[0], "model": current[1]},
                "to": {"provider": target[0], "model": target[1]},
            }
        )
    return {"changed": changed, "unresolved": unresolved}


async def ensure_configured_model_evidence(pack: ProductionPack) -> dict:
    """Create/reuse evidence, then route each role to the best proven model."""

    # Production startup deliberately never mutates bindings.  This command is
    # the explicit operator-approved release path, so it may reconcile aliases
    # and select a better *already tested* target before the release switch.
    from app.database import async_session_factory
    from app.main import ensure_required_bindings
    from app.model_eval.engine import (
        get_catalog_evidence_state,
        run_context_ladder,
        run_qualification,
    )

    await ensure_required_bindings()
    bootstrap = await bootstrap_catalog_and_probes()
    book_id = stable_id(pack.pack_id, "book", "root")

    async with async_session_factory() as db:
        alias_reconciliation = await _reconcile_known_model_aliases(db, book_id)
        await db.commit()
    if alias_reconciliation["changed"]:
        bootstrap = await bootstrap_catalog_and_probes()
    bootstrap["binding_reconciliation"] = alias_reconciliation

    async with async_session_factory() as db:
        initial_targets, missing_bindings = await _effective_targets(db, book_id)

    # Every successfully qualified configured model gets one context profile.
    # This lets the role selector compare candidates without launching a new
    # long-context test merely because a later binding moves between roles.
    all_role_context = _required_context(set(required_roles()))
    total_gateway_calls = 0
    evaluations: dict[tuple[str, str], dict] = {}
    gate_errors: dict[tuple[str, str], dict] = {}

    for (provider, model_id), roles in sorted(initial_targets.items()):
        async with async_session_factory() as db:
            catalog = (
                await db.execute(
                    select(ModelCatalog).where(
                        ModelCatalog.provider == provider,
                        ModelCatalog.model_id == model_id,
                        ModelCatalog.enabled.is_(True),
                        ModelCatalog.availability_status == "available",
                    )
                )
            ).scalar_one_or_none()
            if catalog is None:
                gate_errors[(provider, model_id)] = {
                    "code": "CONFIGURED_MODEL_NOT_DISCOVERED",
                }
                continue
            snapshot = (
                await db.execute(
                    select(ModelHealthSnapshot).where(
                        ModelHealthSnapshot.model_catalog_id == catalog.id
                    )
                )
            ).scalar_one_or_none()
            if not catalog.text_generation_eligible or not catalog.auto_route_enabled:
                gate_errors[(provider, model_id)] = {
                    "code": "CONFIGURED_TEXT_HANDSHAKE_FAILED",
                }
                continue
            if snapshot is None or snapshot.health_status != "healthy":
                gate_errors[(provider, model_id)] = {
                    "code": "CONFIGURED_MODEL_UNHEALTHY",
                    "health_status": snapshot.health_status if snapshot else "missing",
                }
                continue

            ability_run = ModelEvalRun(
                id=uuid.uuid4(),
                model_catalog_id=catalog.id,
                mode="qualification",
                status="queued",
            )
            db.add(ability_run)
            await db.commit()
            ability = await run_qualification(db, ability_run, force=False)
            total_gateway_calls += int(ability.get("gateway_calls") or 0)

            context = None
            if (
                ability.get("status") == "succeeded"
                and ability.get("execution_complete")
                and all_role_context
            ):
                context_run = ModelEvalRun(
                    id=uuid.uuid4(),
                    model_catalog_id=catalog.id,
                    mode="context_ladder",
                    status="queued",
                )
                db.add(context_run)
                await db.commit()
                context = await run_context_ladder(
                    db,
                    context_run,
                    catalog,
                    force=False,
                )
                total_gateway_calls += int(context.get("gateway_calls") or 0)

            await db.refresh(catalog)
            state = await get_catalog_evidence_state(db, catalog)
            evaluations[(provider, model_id)] = {
                "provider": provider,
                "model": model_id,
                "initial_roles": sorted(roles),
                "ability": ability,
                "context": context,
                "state": state,
            }

    assignments, routing_unresolved = _choose_role_assignments(
        evaluations,
        initial_targets,
    )
    async with async_session_factory() as db:
        routing_changed = await _apply_role_assignments(
            db,
            book_id,
            assignments,
        )
        await db.commit()
    async with async_session_factory() as db:
        final_targets, final_missing = await _effective_targets(db, book_id)

    blockers: list[dict] = [
        {"code": "MODEL_BINDING_MISSING", "role": role}
        for role in sorted(set(missing_bindings) | set(final_missing))
    ]
    for (provider, model_id), roles in sorted(final_targets.items()):
        evaluation = evaluations.get((provider, model_id))
        if evaluation is None:
            error = gate_errors.get((provider, model_id)) or {
                "code": "CONFIGURED_MODEL_NOT_EVALUATED",
            }
            blockers.append(
                {
                    **error,
                    "provider": provider,
                    "model": model_id,
                    "roles": sorted(roles),
                }
            )
            continue
        state = evaluation["state"]
        role_failures = [
            role
            for role in sorted(roles)
            if (state.get("role_evidence") or {}).get(role, {}).get("state")
            != "valid"
            or not (state.get("role_evidence") or {}).get(role, {}).get("passed")
        ]
        required_context = _required_context(roles)
        effective_context = (state.get("context_profile") or {}).get("effective")
        if role_failures:
            blockers.append(
                {
                    "code": "MODEL_ROLE_QUALIFICATION_FAILED",
                    "provider": provider,
                    "model": model_id,
                    "roles": role_failures,
                }
            )
        if required_context and (
            effective_context is None or effective_context < required_context
        ):
            blockers.append(
                {
                    "code": "MODEL_CONTEXT_INSUFFICIENT",
                    "provider": provider,
                    "model": model_id,
                    "required_context": required_context,
                    "effective_context": effective_context,
                }
            )

    model_reports: list[dict] = []
    for (provider, model_id), evaluation in sorted(evaluations.items()):
        state = evaluation["state"]
        ability = evaluation["ability"]
        context = evaluation["context"]
        final_roles = sorted(final_targets.get((provider, model_id), set()))
        required_context = _required_context(set(final_roles))
        effective_context = (state.get("context_profile") or {}).get("effective")
        ability_roles = ability.get("roles") or {}
        role_scores = {}
        for role, detail in sorted((state.get("role_evidence") or {}).items()):
            evidence_role = detail.get("evidence_role") or role
            qualification = ability_roles.get(evidence_role) or {}
            role_scores[role] = {
                "state": detail.get("state"),
                "score": detail.get("score"),
                "passed": bool(detail.get("passed")),
                "evidence_role": evidence_role,
                "role_score": qualification.get("role_score"),
                "core_score": qualification.get("core_score"),
                "threshold": qualification.get("threshold"),
                "passed_cases": qualification.get("passed_cases"),
                "total_cases": qualification.get("total_cases"),
                "case_floor_passed": qualification.get("case_floor_passed"),
                "core_floor_passed": qualification.get("core_floor_passed"),
            }
        model_reports.append(
            {
                "provider": provider,
                "model": model_id,
                "initial_roles": evaluation["initial_roles"],
                "roles": final_roles,
                "ability_state": (state.get("ability") or {}).get("state"),
                "ability_reused": bool(ability.get("reused")),
                "ability_gateway_calls": int(ability.get("gateway_calls") or 0),
                "ability_result": {
                    "status": ability.get("status"),
                    "error": ability.get("error"),
                    "reuse_reason": ability.get("reuse_reason"),
                },
                "context_state": (state.get("context") or {}).get("state"),
                "context_reused": bool((context or {}).get("reused")),
                "context_gateway_calls": int((context or {}).get("gateway_calls") or 0),
                "effective_context": effective_context,
                "required_context": required_context or None,
                "role_scores": role_scores,
            }
        )

    return {
        "passed": bool(final_targets) and not blockers,
        "pack_id": pack.pack_id,
        "pack_revision": pack.revision,
        "bootstrap": bootstrap,
        "routing_reconciliation": {
            "changed": routing_changed,
            "unresolved": routing_unresolved,
            "selected": [
                {
                    "role": role,
                    "provider": target[0],
                    "model": target[1],
                }
                for role, target in sorted(assignments.items())
            ],
        },
        "models": model_reports,
        "blockers": blockers,
        "counts": {
            "configured_models": len(final_targets),
            "evaluated_models": len(evaluations),
            "gateway_calls": total_gateway_calls,
            "reused_models": sum(
                1
                for item in evaluations.values()
                if item["ability"].get("reused")
                and (
                    not all_role_context
                    or bool((item.get("context") or {}).get("reused"))
                )
            ),
        },
    }


__all__ = ["ensure_configured_model_evidence"]
