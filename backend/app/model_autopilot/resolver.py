"""Model autopilot: ModelRoutingResolver (spec §48, §46–§47).

Resolution priority:
  1. ChapterRun.model_binding_snapshot   (frozen per-run route)
  2. WritingSession ModelRoutePlan        (per-session route)
  3. Book Auto/Hybrid Policy              (recompute)
  4. Manual AgentModelBinding             (legacy manual)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentModelBinding,
    ChapterRun,
    ModelRoutePlan,
    WritingSession,
)
from app.v74_utils import ModelBindingService

logger = logging.getLogger("novelforge.model_autopilot.resolver")


@dataclass(frozen=True)
class RouteResolution:
    provider: str
    model: str
    fallbacks: list[dict] = field(default_factory=list)
    routing_mode: str = "manual"
    route_plan_id: str | None = None
    frozen_snapshot: bool = False


async def resolve_route(
    db: AsyncSession,
    *,
    agent_role: str,
    book_id: UUID,
    chapter_run_id: UUID | None = None,
) -> RouteResolution:
    # 1. frozen per-run snapshot — never recomputed mid-run (spec §46)
    if chapter_run_id:
        run = (
            await db.execute(select(ChapterRun).where(ChapterRun.id == chapter_run_id))
        ).scalar_one_or_none()
        if run is not None and run.model_binding_snapshot:
            role_snap = (run.model_binding_snapshot.get("roles") or {}).get(agent_role)
            primary = (role_snap or {}).get("primary")
            if primary and primary.get("model"):
                return RouteResolution(
                    provider=primary.get("provider", ""),
                    model=primary["model"],
                    fallbacks=list((role_snap or {}).get("fallbacks") or []),
                    routing_mode=run.model_binding_snapshot.get("routing_mode", "hybrid"),
                    route_plan_id=run.model_binding_snapshot.get("route_plan_id"),
                    frozen_snapshot=True,
                )
            # legacy single-model snapshot
            provider = run.model_binding_snapshot.get("provider")
            model = run.model_binding_snapshot.get("model")
            if provider and model:
                return RouteResolution(provider=provider, model=model, frozen_snapshot=True)

    # 2. active session route plan
    session = (
        await db.execute(
            select(WritingSession)
            .where(
                WritingSession.book_id == book_id,
                WritingSession.status.in_(
                    ("created", "running", "pausing", "paused", "waiting_editorial", "blocked")
                ),
                WritingSession.model_route_plan_id.is_not(None),
            )
            .order_by(WritingSession.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if session is not None and session.model_route_plan_id:
        plan = (
            await db.execute(
                select(ModelRoutePlan).where(ModelRoutePlan.id == session.model_route_plan_id)
            )
        ).scalar_one_or_none()
        if plan is not None and plan.status == "active":
            assignment = (plan.assignments_json or {}).get(agent_role)
            primary = (assignment or {}).get("primary")
            if primary and primary.get("model"):
                return RouteResolution(
                    provider=primary.get("provider", ""),
                    model=primary["model"],
                    fallbacks=list((assignment or {}).get("fallbacks") or []),
                    routing_mode="hybrid",
                    route_plan_id=str(plan.id),
                )

    # 3/4. binding (book scope wins; manual / auto policy)
    svc = ModelBindingService(db)
    binding = await svc.get_binding(agent_role, book_id)
    if binding is not None:
        return RouteResolution(
            provider=binding.provider,
            model=binding.primary_model,
            fallbacks=([{"model": binding.fallback_model, "provider": binding.provider}] if binding.fallback_model else []),
            routing_mode=getattr(binding, "routing_mode", "manual") or "manual",
            route_plan_id=str(binding.routing_policy_id) if binding.routing_policy_id else None,
        )

    raise LookupError(f"No model route for agent_role={agent_role} book_id={book_id}")


async def _legacy_binding(db: AsyncSession, agent_role: str, book_id: UUID) -> AgentModelBinding | None:
    svc = ModelBindingService(db)
    return await svc.get_binding(agent_role, book_id)
