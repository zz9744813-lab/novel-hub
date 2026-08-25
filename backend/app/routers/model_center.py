"""v9.5 Model Center API (spec §67–§73)."""
from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.model_autopilot.capability import apply_manual_capability
from app.model_autopilot.catalog import sync_catalog_from_provider
from app.model_autopilot.health import upsert_health_snapshot
from app.model_autopilot import service as model_service
from app.model_autopilot.probe import probe_model_ping
from app.model_autopilot.scoring import compute_role_score
from app.models import (
    AgentModelBinding,
    ModelCatalog,
    ModelChangeLog,
    ModelHealthProbe,
    ModelRoutingPolicy,
)
from app.v74_utils import ModelBindingService

router = APIRouter(prefix="/api/model-center", tags=["model-center"])


class CapabilityPatch(BaseModel):
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_json_schema: bool | None = None
    supports_reasoning: bool | None = None
    quality_tier: str | None = None
    static_quality_score: float | None = None


class PolicyCreate(BaseModel):
    name: str
    mode: str = "hybrid"
    min_quality_score: float = 0
    min_health_score: float = 0
    require_provider_diversity: bool = True
    fallback_count: int = 2
    allow_degraded: bool = False
    quality_weight: float = 0.45
    reliability_weight: float = 0.25
    context_weight: float = 0.20
    health_weight: float = 0.10
    role_overrides: dict = {}


class BindingPatch(BaseModel):
    routing_mode: str | None = None
    routing_policy_id: uuid.UUID | None = None
    manual_primary_locked: bool | None = None
    manual_fallback_locked: bool | None = None
    allowed_model_ids: list[str] | None = None
    blocked_model_ids: list[str] | None = None


# ── catalog (spec §68) ──


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db)):
    return {"items": await model_service.list_models(db)}


@router.get("/models/{catalog_id}")
async def model_detail(catalog_id: str, db: AsyncSession = Depends(get_db)):
    catalog = (
        await db.execute(select(ModelCatalog).where(ModelCatalog.id == uuid.UUID(catalog_id)))
    ).scalar_one_or_none()
    if catalog is None:
        raise HTTPException(404, "model not found")
    return await model_service.model_row(db, catalog)


@router.post("/sync")
async def sync_now(db: AsyncSession = Depends(get_db)):
    from app.model_autopilot.preflight import _provider_sync_list

    report = {"providers": []}
    for provider, base_url, api_key in await _provider_sync_list(db):
        if not base_url:
            continue
        result = await sync_catalog_from_provider(
            db, provider=provider, base_url=base_url, api_key=api_key or ""
        )
        report["providers"].append({"provider": provider, **result})
    await db.commit()
    return report


# ── health (spec §69) ──


@router.get("/health")
async def health_overview(db: AsyncSession = Depends(get_db)):
    return await model_service.overview(db)


@router.get("/models/{catalog_id}/probes")
async def model_probes(catalog_id: str, limit: int = 50, db: AsyncSession = Depends(get_db)):
    rows = (
        (
            await db.execute(
                select(ModelHealthProbe)
                .where(ModelHealthProbe.model_catalog_id == uuid.UUID(catalog_id))
                .order_by(ModelHealthProbe.started_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": str(r.id),
                "probe_type": r.probe_type,
                "status": r.status,
                "latency_ms": r.latency_ms,
                "error_code": r.error_code,
                "output_valid": r.output_valid,
                "started_at": r.started_at.isoformat() if r.started_at else None,
            }
            for r in rows
        ]
    }


@router.post("/models/{catalog_id}/probe")
async def probe_now(catalog_id: str, db: AsyncSession = Depends(get_db)):
    catalog = (
        await db.execute(select(ModelCatalog).where(ModelCatalog.id == uuid.UUID(catalog_id)))
    ).scalar_one_or_none()
    if catalog is None:
        raise HTTPException(404, "model not found")
    probe = await probe_model_ping(db, catalog)
    db.add(probe)
    await upsert_health_snapshot(db, catalog.id)
    await db.commit()
    return {"status": probe.status, "latency_ms": probe.latency_ms, "error_code": probe.error_code}


@router.post("/probe-all")
async def probe_all(db: AsyncSession = Depends(get_db)):
    catalogs = (await db.execute(select(ModelCatalog))).scalars().all()
    probed = 0
    for catalog in catalogs:
        probe = await probe_model_ping(db, catalog)
        db.add(probe)
        await upsert_health_snapshot(db, catalog.id)
        probed += 1
    await db.commit()
    return {"probed": probed}


# ── capability (spec §70) ──


@router.get("/models/{catalog_id}/capabilities")
async def get_capabilities(catalog_id: str, db: AsyncSession = Depends(get_db)):
    catalog = (
        await db.execute(select(ModelCatalog).where(ModelCatalog.id == uuid.UUID(catalog_id)))
    ).scalar_one_or_none()
    if catalog is None:
        raise HTTPException(404, "model not found")
    row = await model_service.model_row(db, catalog)
    return row["capability"]


@router.patch("/models/{catalog_id}/capabilities")
async def patch_capabilities(catalog_id: str, req: CapabilityPatch, db: AsyncSession = Depends(get_db)):
    """Manual override; writes ModelChangeLog (spec §70)."""
    catalog = (
        await db.execute(select(ModelCatalog).where(ModelCatalog.id == uuid.UUID(catalog_id)))
    ).scalar_one_or_none()
    if catalog is None:
        raise HTTPException(404, "model not found")
    profile = await apply_manual_capability(
        db,
        catalog,
        context_window=req.context_window,
        max_output_tokens=req.max_output_tokens,
        supports_json_schema=req.supports_json_schema,
        supports_reasoning=req.supports_reasoning,
        quality_tier=req.quality_tier,
        static_quality_score=req.static_quality_score,
    )
    # Audit trail for manual capability overrides (spec §70; ModelChangeLog is
    # binding-scoped, so manual capability edits are recorded here).
    from datetime import datetime, timezone

    profile.metadata_json = {
        **(profile.metadata_json or {}),
        "last_manual_override_at": datetime.now(timezone.utc).isoformat(),
        "changed_by": "model-center-api",
    }
    await db.commit()
    return {
        "context_window": profile.context_window,
        "quality_tier": profile.quality_tier,
        "capability_source": profile.capability_source,
    }


# ── scores (spec §71) ──


@router.get("/models/{catalog_id}/scores")
async def model_scores(catalog_id: str, db: AsyncSession = Depends(get_db)):
    catalog = (
        await db.execute(select(ModelCatalog).where(ModelCatalog.id == uuid.UUID(catalog_id)))
    ).scalar_one_or_none()
    if catalog is None:
        raise HTTPException(404, "model not found")
    return (await model_service.model_row(db, catalog))["role_scores"]


@router.get("/role-ranking/{agent_role}")
async def role_ranking(agent_role: str, db: AsyncSession = Depends(get_db)):
    return {"items": await model_service.role_ranking(db, agent_role)}


# ── routing (spec §72) ──


@router.get("/routing")
async def routing_view(db: AsyncSession = Depends(get_db)):
    return {"items": await model_service.current_routes(db)}


@router.get("/routes/current")
async def routes_current(db: AsyncSession = Depends(get_db)):
    return {"items": await model_service.current_routes(db)}


@router.get("/routes/timeline")
async def routes_timeline(limit: int = 50, db: AsyncSession = Depends(get_db)):
    return {"items": await model_service.route_timeline(db, limit)}


@router.post("/routes/recalculate")
async def recalculate(db: AsyncSession = Depends(get_db)):
    """Recompute per-role rankings from current signals."""
    roles = ("chapter_planner", "draft_writer", "review_agent", "state_extractor", "style_analyzer")
    for catalog in (await db.execute(select(ModelCatalog))).scalars().all():
        for role in roles:
            await compute_role_score(db, catalog, role)
    await db.commit()
    return {"recomputed": True}


# ── bindings / manual overrides (spec §80) ──


@router.patch("/bindings/{binding_id}")
async def patch_binding(binding_id: str, req: BindingPatch, db: AsyncSession = Depends(get_db)):
    binding = (
        await db.execute(
            select(AgentModelBinding).where(AgentModelBinding.id == uuid.UUID(binding_id))
        )
    ).scalar_one_or_none()
    if binding is None:
        raise HTTPException(404, "binding not found")
    if req.routing_mode is not None:
        binding.routing_mode = req.routing_mode
    if req.routing_policy_id is not None:
        binding.routing_policy_id = req.routing_policy_id
    if req.manual_primary_locked is not None:
        binding.manual_primary_locked = req.manual_primary_locked
    if req.manual_fallback_locked is not None:
        binding.manual_fallback_locked = req.manual_fallback_locked
    if req.allowed_model_ids is not None:
        binding.allowed_model_ids = req.allowed_model_ids
    if req.blocked_model_ids is not None:
        binding.blocked_model_ids = req.blocked_model_ids
    await db.commit()
    return {
        "id": str(binding.id),
        "routing_mode": binding.routing_mode,
        "manual_primary_locked": binding.manual_primary_locked,
    }


@router.post("/enable-healthy")
async def enable_healthy_auto_route(db: AsyncSession = Depends(get_db)):
    """Bulk-promote currently healthy catalog models to auto-route eligible."""
    rows = (
        (
            await db.execute(
                select(ModelCatalog)
                .where(
                    ModelCatalog.enabled.is_(True),
                    ModelCatalog.availability_status != "missing",
                    ModelCatalog.auto_route_enabled.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    enabled = 0
    for catalog in rows:
        from app.models import ModelHealthSnapshot

        snap = (
            await db.execute(
                select(ModelHealthSnapshot).where(
                    ModelHealthSnapshot.model_catalog_id == catalog.id
                )
            )
        ).scalar_one_or_none()
        # only promote models that are NOT unavailable / missing
        if snap is not None and snap.health_status in ("unavailable", "unknown") and snap.consecutive_failures:
            continue
        catalog.auto_route_enabled = True
        enabled += 1
    await db.commit()
    return {"enabled": enabled}


@router.post("/models/{catalog_id}/enable")
async def enable_auto_route(catalog_id: str, db: AsyncSession = Depends(get_db)):
    """Promote a model to auto-route eligible (after probe/benchmark, spec §58)."""
    catalog = (
        await db.execute(select(ModelCatalog).where(ModelCatalog.id == uuid.UUID(catalog_id)))
    ).scalar_one_or_none()
    if catalog is None:
        raise HTTPException(404, "model not found")
    catalog.auto_route_enabled = True
    await db.commit()
    return {"ok": True, "model": catalog.model_id}


# ── policy (spec §73) ──


@router.get("/policies")
async def list_policies(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ModelRoutingPolicy).order_by(ModelRoutingPolicy.name))).scalars().all()
    return {
        "items": [
            {
                "id": str(p.id),
                "name": p.name,
                "mode": p.mode,
                "fallback_count": p.fallback_count,
                "min_quality_score": p.min_quality_score,
                "require_provider_diversity": p.require_provider_diversity,
                "enabled": p.enabled,
            }
            for p in rows
        ]
    }


@router.post("/policies")
async def create_policy(req: PolicyCreate, db: AsyncSession = Depends(get_db)):
    policy = ModelRoutingPolicy(
        id=uuid.uuid4(),
        name=req.name,
        mode=req.mode,
        min_quality_score=req.min_quality_score,
        min_health_score=req.min_health_score,
        require_provider_diversity=req.require_provider_diversity,
        fallback_count=req.fallback_count,
        allow_degraded=req.allow_degraded,
        quality_weight=req.quality_weight,
        reliability_weight=req.reliability_weight,
        context_weight=req.context_weight,
        health_weight=req.health_weight,
        role_overrides_json=req.role_overrides,
    )
    db.add(policy)
    await db.commit()
    return {"id": str(policy.id), "name": policy.name, "mode": policy.mode}
