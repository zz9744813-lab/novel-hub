"""Model autopilot: service layer for the Model Center (spec §65–§85)."""
from __future__ import annotations

from collections import Counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model_autopilot.health import refresh_all_snapshots
from app.models import (
    ModelCatalog,
    ModelCapabilityProfile,
    ModelHealthSnapshot,
    ModelRoleScore,
    ModelRoutePlan,
    ModelRouteEvent,
)


async def overview(db: AsyncSession) -> dict:
    """Dashboard totals (spec §74)."""
    catalogs = list((await db.execute(select(ModelCatalog))).scalars().all())
    snapshots = {
        s.model_catalog_id: s
        for s in (await db.execute(select(ModelHealthSnapshot))).scalars().all()
    }
    health_counter: Counter = Counter()
    provider_models: Counter = Counter()
    for c in catalogs:
        snap = snapshots.get(c.id)
        status = snap.health_status if snap else "unknown"
        health_counter[status] += 1
        provider_models[c.provider] += 1

    inactive_sessions = (
        await db.execute(
            select(ModelRoutePlan.writing_session_id).where(
                ModelRoutePlan.writing_session_id.is_not(None),
                ModelRoutePlan.status == "active",
            )
        )
    ).scalars().all()

    return {
        "providers": len(provider_models),
        "models": len(catalogs),
        "healthy": health_counter.get("healthy", 0),
        "degraded": health_counter.get("degraded", 0),
        "unstable": health_counter.get("unstable", 0),
        "unavailable": health_counter.get("unavailable", 0),
        "unknown": health_counter.get("unknown", 0),
        "by_provider": dict(provider_models),
        "active_route_plans": len(inactive_sessions),
        "alerts": await _alerts(db, catalogs, snapshots),
    }


async def _alerts(db: AsyncSession, catalogs: list[ModelCatalog], snapshots: dict) -> list[dict]:
    alerts: list[dict] = []
    for c in catalogs:
        snap = snapshots.get(c.id)
        if snap is None:
            continue
        if snap.health_status == "unavailable":
            alerts.append({"level": "danger", "message": f"模型不可用: {c.model_id}"})
        elif snap.health_status == "degraded":
            alerts.append({"level": "warning", "message": f"模型降级: {c.model_id}"})
    return alerts[:20]


async def list_models(db: AsyncSession) -> list[dict]:
    """Full model table rows (spec §76)."""
    catalogs = list((await db.execute(select(ModelCatalog).order_by(ModelCatalog.provider))).scalars().all())
    return [await model_row(db, c) for c in catalogs]


async def model_row(db: AsyncSession, c: ModelCatalog) -> dict:
    snap = (
        await db.execute(
            select(ModelHealthSnapshot).where(ModelHealthSnapshot.model_catalog_id == c.id)
        )
    ).scalar_one_or_none()
    cap = (
        await db.execute(
            select(ModelCapabilityProfile).where(
                ModelCapabilityProfile.model_catalog_id == c.id
            )
        )
    ).scalar_one_or_none()
    scores = (
        (
            await db.execute(
                select(ModelRoleScore).where(ModelRoleScore.model_catalog_id == c.id)
            )
        )
        .scalars()
        .all()
    )
    return {
        "id": str(c.id),
        "provider": c.provider,
        "model_id": c.model_id,
        "display_name": c.display_name,
        "enabled": c.enabled,
        "auto_route_enabled": c.auto_route_enabled,
        "availability_status": c.availability_status,
        "discovery_source": c.discovery_source,
        "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
        "health": {
            "status": snap.health_status if snap else "unknown",
            "success_rate_15m": snap.success_rate_15m if snap else None,
            "success_rate_1h": snap.success_rate_1h if snap else None,
            "success_rate_24h": snap.success_rate_24h if snap else None,
            "p50_latency_ms": snap.p50_latency_ms if snap else None,
            "p95_latency_ms": snap.p95_latency_ms if snap else None,
            "consecutive_failures": snap.consecutive_failures if snap else 0,
            "health_score": snap.health_score if snap else None,
            "last_probe_at": snap.last_probe_at.isoformat() if snap and snap.last_probe_at else None,
        },
        "capability": {
            "context_window": cap.context_window if cap else None,
            "max_output_tokens": cap.max_output_tokens if cap else None,
            "supports_json_schema": cap.supports_json_schema if cap else False,
            "supports_reasoning": cap.supports_reasoning if cap else False,
            "quality_tier": cap.quality_tier if cap else "unknown",
            "static_quality_score": cap.static_quality_score if cap else None,
        },
        "role_scores": {
            s.agent_role: {
                "composite_score": s.composite_score,
                "reliability_score": s.reliability_score,
                "human_quality_score": s.human_quality_score,
                "sample_count": s.sample_count,
            }
            for s in scores
        },
    }


async def role_ranking(db: AsyncSession, agent_role: str) -> list[dict]:
    """Ranked candidates for one role (spec §71)."""
    rows = (
        (
            await db.execute(
                select(ModelRoleScore, ModelCatalog)
                .join(ModelCatalog, ModelCatalog.id == ModelRoleScore.model_catalog_id)
                .where(ModelRoleScore.agent_role == agent_role)
                .order_by(ModelRoleScore.composite_score.desc().nulls_last())
            )
        )
        .all()
    )
    result = []
    for score, catalog in rows:
        snap = (
            await db.execute(
                select(ModelHealthSnapshot).where(
                    ModelHealthSnapshot.model_catalog_id == catalog.id
                )
            )
        ).scalar_one_or_none()
        result.append(
            {
                "catalog_id": str(catalog.id),
                "provider": catalog.provider,
                "model": catalog.model_id,
                "composite_score": score.composite_score,
                "reliability_score": score.reliability_score,
                "human_quality_score": score.human_quality_score,
                "sample_count": score.sample_count,
                "health_status": snap.health_status if snap else "unknown",
            }
        )
    return result


async def route_timeline(db: AsyncSession, limit: int = 50) -> list[dict]:
    """Recent route events (spec §84 reuses ModelRouteEvent)."""
    # ModelRouteEvent has no timestamp column; cap by insertion order.
    rows = (
        (
            await db.execute(
                select(ModelRouteEvent).order_by(ModelRouteEvent.id.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "run_id": str(r.run_id) if getattr(r, "run_id", None) else None,
            "provider": str(getattr(r, "actual_provider", None) or ""),
            "model": str(getattr(r, "actual_model", None) or ""),
            "route_type": str(getattr(r, "route_type", None) or ""),
            "reason": str(getattr(r, "reason", None) or ""),
            "created_at": None,
        }
        for r in rows
    ]


async def current_routes(db: AsyncSession) -> list[dict]:
    """Current per-role primary/fallback assignments (spec §78)."""
    roles = ("chapter_planner", "draft_writer", "review_agent", "state_extractor", "style_analyzer")
    from app.v74_utils import ModelBindingService

    svc = ModelBindingService(db)
    out = []
    for role in roles:
        binding = await svc.get_binding(role, None)
        out.append(
            {
                "agent_role": role,
                "mode": (binding.routing_mode if binding else "manual") or "manual",
                "primary": (
                    {"provider": binding.provider, "model": binding.primary_model} if binding else None
                ),
                "fallbacks": (
                    [{"provider": binding.provider, "model": binding.fallback_model}]
                    if binding and binding.fallback_model
                    else []
                ),
                "binding_id": str(binding.id) if binding else None,
            }
        )
    return out


async def refresh_snapshots(db: AsyncSession) -> int:
    return await refresh_all_snapshots(db)
