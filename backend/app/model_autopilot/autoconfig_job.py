"""v9.6: model detection / one-click autoconfigure jobs (spec §94–§99, §38–§41).

run_model_detection   — sync → due lightweight health → capability → read the
                        versioned evidence state → recommendation (never runs
                        ability/context/performance generation).
run_model_autoconfigure — reuse ≤30min detection (or run one), snapshot,
                        atomically apply every required role binding, verify,
                        ModelChangeLog, then success. Any required-role gap
                        ROLLS BACK entirely (no half-config, spec §97).

Both write progress to model_autoconfig_runs so the UI shows real phases.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model_autopilot.catalog import ensure_capability_for_catalog, sync_catalog_from_provider
from app.model_autopilot.capability import DEFAULT_ROLE_QUALITY_FLOOR
from app.model_autopilot.health import upsert_health_snapshot
from app.model_autopilot.probe import probe_model_ping
from app.model_autopilot.router import build_role_route, default_policy_for
from app.model_autopilot.scoring import compute_role_score
from app.model_autopilot.classification import eligible_text_candidates
from app.models import (
    AgentModelBinding,
    ModelAutoConfigRun,
    ModelCatalog,

    ModelChangeLog,
    ModelHealthSnapshot,
)

logger = logging.getLogger("novelforge.model_autopilot.autoconfig")

from app.agents.registry import ROLE_REGISTRY

REQUIRED_ROLES = sorted(
    role for role, spec in ROLE_REGISTRY.items() if spec.production and spec.model_required
)

ROLE_DISPLAY = {role: ROLE_REGISTRY[role].display_name for role in REQUIRED_ROLES}

VALID_MINUTES = 30
POLICY = default_policy_for(
    "hybrid",
    quality_weight=0.45,
    reliability_weight=0.20,
    context_weight=0.15,
    health_weight=0.10,
    latency_weight=0.0,
    cost_weight=0.0,
)


# ── helpers ──


async def _update_run(db: AsyncSession, run: ModelAutoConfigRun, **kw) -> None:
    for key, value in kw.items():
        setattr(run, key, value)


async def _get_models(db: AsyncSession) -> list[ModelCatalog]:
    return list(
        (
            await db.execute(
                select(ModelCatalog).where(
                    ModelCatalog.enabled.is_(True),
                    ModelCatalog.availability_status == "available",
                )
            )
        ).scalars()
    )


# ── detection (spec §95) ──


async def run_model_detection(db: AsyncSession, run: ModelAutoConfigRun) -> dict:
    from app.model_autopilot.preflight import _provider_sync_list
    from app.model_autopilot.jobs import _probe_due
    from app.model_eval.engine import (
        ABILITY_EVALUATOR_REVISION,
        CONTEXT_EVALUATOR_REVISION,
        ensure_v98_suites,
        get_catalog_evidence_state,
    )

    now = datetime.now(timezone.utc)
    run.started_at = run.started_at or now
    run.status = "running"

    # phase 0: catalog sync (tolerant of provider failures, spec §99)
    run.phase = "catalog_sync"
    run.progress = 5
    await db.commit()
    for provider, base_url, api_key in await _provider_sync_list(db):
        if not base_url:
            continue
        try:
            await sync_catalog_from_provider(
                db, provider=provider, base_url=base_url, api_key=api_key or ""
            )
            await db.commit()
        except Exception as e:  # noqa: BLE001 - provider failure isolated
            logger.warning("catalog sync failed %s: %s", provider, e)
            await db.rollback()

    models = eligible_text_candidates(await _get_models(db))
    run.detected_models = len(models)
    run.total = len(models)

    await ensure_v98_suites(db)
    await db.commit()

    # Routine detection performs only due L1 pings.  Performance and full
    # capability generation are explicit/low-frequency jobs, never blockers.
    run.phase = "provider_check"
    run.progress = 15
    await db.commit()

    healthy = 0
    health_ttl_seconds = int(os.environ.get("MODEL_PREWRITE_HEALTH_TTL_SECONDS", "300"))
    health_interval_minutes = max(1, (health_ttl_seconds + 59) // 60)
    run.phase = "model_health"
    run.progress = 20
    await db.commit()
    for idx, catalog in enumerate(models):
        run.current_model = catalog.model_id
        run.finished = idx
        run.progress = 20 + int(40 * (idx / max(1, len(models))))
        await db.commit()
        if not await _probe_due(db, catalog, interval_minutes=health_interval_minutes):
            snapshot = (
                await db.execute(
                    select(ModelHealthSnapshot).where(
                        ModelHealthSnapshot.model_catalog_id == catalog.id
                    )
                )
            ).scalar_one_or_none()
            if snapshot and snapshot.health_status in ("healthy", "degraded"):
                healthy += 1
            continue
        try:
            probe = await probe_model_ping(db, catalog)
            db.add(probe)
            await db.flush()
            await upsert_health_snapshot(db, catalog.id)
            await db.commit()
            snap = (
                await db.execute(
                    select(ModelHealthSnapshot).where(
                        ModelHealthSnapshot.model_catalog_id == catalog.id
                    )
                )
            ).scalar_one_or_none()
            if snap and snap.health_status in ("healthy", "degraded"):
                healthy += 1
        except Exception as e:  # noqa: BLE001
            logger.debug("health probe failed %s: %s", catalog.model_id, e)
            await db.rollback()

    run.healthy_models = healthy
    run.phase = "capability"
    run.progress = 70
    await db.commit()
    for catalog in models:
        await ensure_capability_for_catalog(db, catalog)

    # Detection and auto-configuration never run the expensive full
    # qualification benchmark. They only reuse existing one-time
    # ability evidence. Models without a valid ability_evaluation_key are
    # reported with ``needs_qualification=True`` so the UI can prompt a
    # deliberate /qualify run; the full suite is never started implicitly.
    run.phase = "role_evidence"
    run.progress = 86
    await db.commit()
    needs_qualification: list[str] = []
    needs_qualification_detail: list[dict] = []
    for catalog in models:
        state = await get_catalog_evidence_state(db, catalog)
        if state.get("ability", {}).get("state") != "valid":
            needs_qualification.append(catalog.model_id)
            needs_qualification_detail.append(
                {
                    "catalog_id": str(catalog.id),
                    "provider": catalog.provider,
                    "model": catalog.model_id,
                    "state": state.get("ability", {}).get("state"),
                    "reason": state.get("ability", {}).get("reason"),
                    "changed_fields": state.get("ability", {}).get("changed_fields") or [],
                }
            )
    # recompute composite; benchmark_score (if any prior qualification exists)
    # is preserved by compute_role_score (v9.8 P0-2 fix) and folded in.
    for catalog in models:
        for role in REQUIRED_ROLES:
            await compute_role_score(db, catalog, role)

    run.phase = "recommendation"
    run.progress = 94
    await db.commit()

    recommendation, eligible = await build_recommendation(db, models)
    recommendation["needs_qualification"] = needs_qualification
    recommendation["needs_qualification_detail"] = needs_qualification_detail
    recommendation["evidence_revision"] = {
        "ability": ABILITY_EVALUATOR_REVISION,
        "context": CONTEXT_EVALUATOR_REVISION,
    }
    run.eligible_models = eligible
    run.recommendation_json = recommendation
    run.phase = "done"
    run.status = "succeeded"
    run.progress = 100
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    return recommendation


async def build_recommendation(db: AsyncSession, models: list[ModelCatalog]) -> tuple[dict, int]:
    """Per-role primary/fallback recommendation from current signals (spec §59, §12)."""
    from app.agents.registry import ROLE_REGISTRY

    recommendation = {}
    eligible = 0
    for role in REQUIRED_ROLES:
        spec = ROLE_REGISTRY[role]
        floor = spec.default_quality_floor or DEFAULT_ROLE_QUALITY_FLOOR.get(role, 70)
        result = await build_role_route(
            db,
            agent_role=role,
            required_context=spec.expected_context_tokens,
            policy=POLICY,
            locked_primary=None,
        )
        if result.assignment:
            eligible += 1
        recommendation[role] = {
            **(result.assignment or {"primary": None, "fallbacks": []}),
            "minimum_quality_score": floor,
            "blockers": result.blockers or [],
        }
    return recommendation, eligible


# ── one-click autoconfigure (spec §96–§97) ──


async def run_model_autoconfigure(db: AsyncSession, run: ModelAutoConfigRun) -> dict:
    from app.v74_utils import ModelBindingService

    # 1. reuse a fresh detection (< 30min) or run one now
    recommendation = run.recommendation_json
    if recommendation is None:
        recommendation = await run_model_detection(db, run)

    # verify all roles have primary + fallback1 (spec §98: P0 = 100%/100%)
    missing = []
    for role in REQUIRED_ROLES:
        assign = recommendation.get(role) or {}
        if not assign.get("primary", {}).get("model"):
            missing.append(role)
        fb1 = (assign.get("fallbacks") or [{}])[0].get("model")
        if not fb1:
            missing.append(f"{role}:fallback1")
    if missing:
        run.status = "failed"
        run.error_json = {"code": "REQUIRED_ROLE_COVERAGE", "missing": missing}
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "failed", "missing": missing}

    # 2. snapshot current config (spec §29/§61)
    svc = ModelBindingService(db)
    bindings: dict[str, AgentModelBinding] = {}
    before = {}
    for role in REQUIRED_ROLES:
        binding = await svc.get_binding(role, None)
        before[role] = {
            "provider": binding.provider if binding else None,
            "primary_model": binding.primary_model if binding else None,
            "fallback_model": binding.fallback_model if binding else None,
            "routing_mode": binding.routing_mode if binding else None,
        }
    run.before_snapshot = before

    # 3. apply atomically inside this transaction (rollback => whole thing)
    try:
        from app.models import AgentModelBinding as AMB

        for role in REQUIRED_ROLES:
            assign = recommendation[role]
            primary = assign["primary"]
            fb1 = (assign.get("fallbacks") or [{}])[0]
            binding = await svc.get_binding(role, None)
            if binding is None:
                binding = AMB(
                    id=uuid.uuid4(),
                    scope_type="global",
                    scope_id=None,
                    agent_role=role,
                    provider=primary["provider"],
                    primary_model=primary["model"],
                    fallback_model=fb1.get("model"),
                    reasoning_mode="auto",
                    routing_mode="hybrid",
                    updated_by="model-autopilot",
                )
                db.add(binding)
                await db.flush()
            else:
                binding.provider = primary["provider"]
                binding.primary_model = primary["model"]
                binding.fallback_model = fb1.get("model")
                binding.routing_mode = "hybrid"
            binding.auto_assignment_snapshot = {
                "primary": primary,
                "fallbacks": assign.get("fallbacks") or [],
                "scores": {"role_quality_floor": assign.get("minimum_quality_score")},
                "configured_at": datetime.now(timezone.utc).isoformat(),
            }
            binding.last_auto_config_run_id = run.id
            db.add(
                ModelChangeLog(
                    id=uuid.uuid4(),
                    binding_id=binding.id,
                    agent_role=role,
                    old_provider=before[role]["provider"],
                    old_model=before[role]["primary_model"],
                    new_provider=primary["provider"],
                    new_model=primary["model"],
                    old_reasoning_mode="auto",
                    new_reasoning_mode="auto",
                    reason=f"AUTO_CONFIG run={run.id}",
                    changed_by="model-autopilot",
                )
            )
        await db.commit()
    except Exception as e:  # noqa: BLE001 - atomic apply: rollback everything
        await db.rollback()
        run.status = "failed"
        run.error_json = {"code": "APPLY_FAILED", "detail": str(e)[:500]}
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "failed", "error": str(e)}

    after = {}
    for role in REQUIRED_ROLES:
        binding = await svc.get_binding(role, None)
        after[role] = {
            "provider": binding.provider,
            "primary_model": binding.primary_model,
            "fallback_model": binding.fallback_model,
        }
    run.after_snapshot = after
    run.status = "succeeded"
    run.phase = "done"
    run.progress = 100
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "succeeded", "applied": len(REQUIRED_ROLES)}


# ── ARQ wrappers ──


async def run_model_detection_job(ctx, run_id: str) -> dict:
    """ARQ: run a persisted detect run (spec §103: user-triggered evaluation)."""
    from app.database import async_session_factory

    try:
        async with async_session_factory() as db:
            run = (
                await db.execute(
                    select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
                )
            ).scalar_one_or_none()
            if run is None or run.status not in ("queued", "running"):
                return {"status": "skipped"}
        async with async_session_factory() as db:
            run = (
                await db.execute(
                    select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
                )
            ).scalar_one()
            await run_model_detection(db, run)
        return {"status": "succeeded", "run_id": run_id}
    except Exception as e:  # noqa: BLE001
        try:
            async with async_session_factory() as db:
                run = (
                    await db.execute(
                        select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
                    )
                ).scalar_one_or_none()
                if run is not None:
                    run.status = "failed"
                    run.error_json = {"code": "DETECT_FAILED", "detail": str(e)[:500]}
                    run.finished_at = datetime.now(timezone.utc)
                    await db.commit()
                    return {"status": "failed", "error": str(e)}
        except Exception:  # noqa: BLE001
            pass
        raise


async def run_model_autoconfigure_job(ctx, run_id: str) -> dict:
    """ARQ: one-click config — reuses a fresh detection or runs one (spec §96)."""
    from app.database import async_session_factory

    try:
        async with async_session_factory() as db:
            run = (
                await db.execute(
                    select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
                )
            ).scalar_one_or_none()
            if run is None:
                return {"status": "skipped"}
            run.action = "detect_and_configure"
            await db.commit()
        async with async_session_factory() as db:
            run = (
                await db.execute(
                    select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
                )
            ).scalar_one()
            return await run_model_autoconfigure(db, run)
    except Exception as e:  # noqa: BLE001
        raise


async def rollback_auto_config(db: AsyncSession, run: ModelAutoConfigRun) -> dict:
    """Spec §29: restore the before_snapshot of a finished configure run."""
    from app.v74_utils import ModelBindingService

    if run.action != "detect_and_configure" or not run.before_snapshot:
        return {"status": "noop", "reason": "not a configure run"}
    svc = ModelBindingService(db)
    restored = 0
    for role, snap in (run.before_snapshot or {}).items():
        binding = await svc.get_binding(role, None)
        if binding is not None:
            binding.provider = snap.get("provider") or binding.provider
            binding.primary_model = snap.get("primary_model") or binding.primary_model
            binding.fallback_model = snap.get("fallback_model")
            binding.routing_mode = snap.get("routing_mode") or "manual"
            db.add(
                ModelChangeLog(
                    id=uuid.uuid4(),
                    binding_id=binding.id,
                    agent_role=role,
                    old_provider=run.after_snapshot.get(role, {}).get("provider"),
                    old_model=run.after_snapshot.get(role, {}).get("primary_model"),
                    new_provider=snap.get("provider") or binding.provider,
                    new_model=snap.get("primary_model") or binding.primary_model,
                    old_reasoning_mode="auto",
                    new_reasoning_mode="auto",
                    reason=f"AUTO_CONFIG_ROLLBACK run={run.id}",
                    changed_by="model-autopilot",
                )
            )
            restored += 1
    return {"status": "restored", "restored": restored}
