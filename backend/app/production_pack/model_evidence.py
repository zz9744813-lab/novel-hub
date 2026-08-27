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
    """Create/reuse evidence for every model effectively bound to pack roles."""

    # Production startup deliberately never mutates bindings.  This CLI is an
    # explicit release action, so installing only missing env-backed bindings
    # here preserves that approval boundary while allowing a fresh deployment
    # to become ready.
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
        reconciliation = await _reconcile_known_model_aliases(db, book_id)
        await db.commit()
    if reconciliation["changed"]:
        # The first pass synchronized the provider catalogs.  Re-run the cheap
        # handshake layer against the corrected bindings; fresh successful
        # probes are reused and therefore do not repeat network work.
        bootstrap = await bootstrap_catalog_and_probes()
    bootstrap["binding_reconciliation"] = reconciliation

    async with async_session_factory() as db:
        targets, missing_bindings = await _effective_targets(db, book_id)

    blockers: list[dict] = [
        {"code": "MODEL_BINDING_MISSING", "role": role}
        for role in missing_bindings
    ]
    model_reports: list[dict] = []
    total_gateway_calls = 0

    for (provider, model_id), roles in sorted(targets.items()):
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
                blockers.append(
                    {
                        "code": "CONFIGURED_MODEL_NOT_DISCOVERED",
                        "provider": provider,
                        "model": model_id,
                        "roles": sorted(roles),
                    }
                )
                continue
            snapshot = (
                await db.execute(
                    select(ModelHealthSnapshot).where(
                        ModelHealthSnapshot.model_catalog_id == catalog.id
                    )
                )
            ).scalar_one_or_none()
            if not catalog.text_generation_eligible or not catalog.auto_route_enabled:
                blockers.append(
                    {
                        "code": "CONFIGURED_TEXT_HANDSHAKE_FAILED",
                        "provider": provider,
                        "model": model_id,
                        "roles": sorted(roles),
                    }
                )
                continue
            if snapshot is None or snapshot.health_status != "healthy":
                blockers.append(
                    {
                        "code": "CONFIGURED_MODEL_UNHEALTHY",
                        "provider": provider,
                        "model": model_id,
                        "health_status": snapshot.health_status if snapshot else "missing",
                    }
                )
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
            required_context = _required_context(roles)
            if (
                ability.get("status") == "succeeded"
                and ability.get("execution_complete")
                and required_context
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
            role_failures = [
                role
                for role in sorted(roles)
                if (state.get("role_evidence") or {}).get(role, {}).get("state")
                != "valid"
                or not (state.get("role_evidence") or {}).get(role, {}).get("passed")
            ]
            effective_context = (
                (state.get("context_profile") or {}).get("effective")
            )
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

            model_reports.append(
                {
                    "provider": provider,
                    "model": model_id,
                    "roles": sorted(roles),
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
                    "role_scores": {
                        role: {
                            "state": detail.get("state"),
                            "score": detail.get("score"),
                            "passed": bool(detail.get("passed")),
                            "evidence_role": detail.get("evidence_role"),
                        }
                        for role, detail in sorted(
                            (state.get("role_evidence") or {}).items()
                        )
                    },
                }
            )

    return {
        "passed": bool(model_reports) and not blockers,
        "pack_id": pack.pack_id,
        "pack_revision": pack.revision,
        "bootstrap": bootstrap,
        "models": model_reports,
        "blockers": blockers,
        "counts": {
            "configured_models": len(targets),
            "evaluated_models": len(model_reports),
            "gateway_calls": total_gateway_calls,
            "reused_models": sum(
                1
                for item in model_reports
                if item["ability_reused"]
                and (item["required_context"] is None or item["context_reused"])
            ),
        },
    }


__all__ = ["ensure_configured_model_evidence"]
