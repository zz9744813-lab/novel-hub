"""Model autopilot: routing engine (spec §32, §35–§42, §45, §47, §82–§83).

Score = 0.45*RoleQuality + 0.25*Reliability + 0.20*ContextFit + 0.10*Health.
Hard eligibility first; provider-diverse fallbacks; draft writer floor.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model_autopilot.capability import (
    CONTEXT_REQUIRED_ROLES,
    DEFAULT_ROLE_QUALITY_FLOOR,
    context_fit_score,
)
from app.models import (
    ModelCapabilityProfile,
    ModelCatalog,
    ModelHealthSnapshot,
    ModelRoleScore,
    ModelRoutingPolicy,
)

logger = logging.getLogger("novelforge.model_autopilot.router")

DEFAULT_WEIGHTS = {
    "quality": 0.45,
    "reliability": 0.20,
    "context": 0.15,
    "health": 0.10,
    "performance": 0.10,
}


async def compute_performance_score(db: AsyncSession, catalog_id: UUID) -> float | None:
    """v9.6 §57: TTFT 40% + tokens/s 40% + latency 20%. None when no data."""
    from app.models import ModelHealthProbe

    perf = (
        await db.execute(
            select(ModelHealthProbe)
            .where(
                ModelHealthProbe.model_catalog_id == catalog_id,
                ModelHealthProbe.probe_type == "performance",
            )
            .order_by(ModelHealthProbe.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if perf is None or perf.tokens_per_second is None:
        return None
    ttft_ms = perf.first_token_ms or 2000
    tps = perf.tokens_per_second or 0

    def _band(value: float, bands: list[tuple[float, float]]) -> float:
        for threshold, score in bands:
            if value <= threshold:
                return score
        return bands[-1][1]

    ttft_score = _band(ttft_ms, [(1000, 100), (3000, 70), (6000, 40), (10**9, 20)])
    tps_score = _band(tps, [(0, 20), (10, 40), (20, 70), (10**9, 100)])
    latency_score = _band((perf.latency_ms or 10000), [(4000, 100), (10000, 70), (25000, 40), (10**9, 20)])
    return round(0.4 * ttft_score + 0.4 * tps_score + 0.2 * latency_score, 1)


@dataclass(frozen=True)
class RouteTarget:
    provider: str
    model: str
    route_score: float
    reasons: list[str] = ()


@dataclass(frozen=True)
class RoleRouteResult:
    assignment: dict | None  # {"primary": {...}, "fallbacks": [...]}
    blockers: list[dict] | None  # when no eligible model
    candidates: list[RouteTarget] | None = None


async def _catalog_rows(db: AsyncSession) -> list[ModelCatalog]:
    return list((await db.execute(select(ModelCatalog))).scalars().all())


async def build_role_route(
    db: AsyncSession,
    *,
    agent_role: str,
    required_context: int,
    policy: dict | None = None,
    allowed_ids: list[str] | None = None,
    blocked_ids: list[str] | None = None,
    locked_primary: dict | None = None,
) -> RoleRouteResult:
    """Pick primary + fallbacks for one role (spec §37–§41, §44)."""
    policy = policy or {}
    fallback_count = int(policy.get("fallback_count", 2))
    require_diversity = bool(policy.get("require_provider_diversity", True))
    min_health = float(policy.get("min_health_score", 0))
    min_quality = float(policy.get("min_quality_score", 0))
    allow_degraded = bool(policy.get("allow_degraded", False))
    health_ttl_seconds = int(
        policy.get("prewrite_health_ttl_seconds")
        or os.environ.get("MODEL_PREWRITE_HEALTH_TTL_SECONDS", "300")
    )
    weights = {
        "quality": float(policy.get("quality_weight", DEFAULT_WEIGHTS["quality"])),
        "reliability": float(policy.get("reliability_weight", DEFAULT_WEIGHTS["reliability"])),
        "context": float(policy.get("context_weight", DEFAULT_WEIGHTS["context"])),
        "health": float(policy.get("health_weight", DEFAULT_WEIGHTS["health"])),
        "performance": float(policy.get("performance_weight", 0.10)),
    }
    floor = float(
        (policy.get("role_overrides") or {}).get(agent_role, {}).get("minimum_quality_score")
        or DEFAULT_ROLE_QUALITY_FLOOR.get(agent_role, 70)
    )
    requires_context = agent_role in CONTEXT_REQUIRED_ROLES

    catalogs = await _catalog_rows(db)
    scored: list[tuple[float, RouteTarget, dict]] = []
    blockers = []

    for catalog in catalogs:
        if not catalog.enabled:
            continue
        if catalog.availability_status == "missing":
            continue
        if blocked_ids and str(catalog.id) in blocked_ids:
            blockers.append(
                {
                    "model": catalog.model_id,
                    "role": agent_role,
                    "code": "MODEL_BLOCKED_BY_BINDING",
                }
            )
            continue
        if allowed_ids and str(catalog.id) not in allowed_ids:
            blockers.append(
                {
                    "model": catalog.model_id,
                    "role": agent_role,
                    "code": "MODEL_NOT_ALLOWED_BY_BINDING",
                }
            )
            continue
        if not catalog.auto_route_enabled:
            blockers.append(
                {
                    "model": catalog.model_id,
                    "role": agent_role,
                    "code": "MODEL_AUTO_ROUTE_DISABLED",
                }
            )
            continue  # spec §37: never auto-route a model that hasn't passed probe/benchmark
        if not catalog.text_generation_eligible:
            blockers.append(
                {
                    "model": catalog.model_id,
                    "role": agent_role,
                    "code": "MODEL_NOT_TEXT_ELIGIBLE",
                }
            )
            continue

        from app.model_eval.engine import get_catalog_evidence_state

        evidence = await get_catalog_evidence_state(db, catalog)
        ability_state = evidence.get("ability") or {}
        role_evidence = (evidence.get("role_evidence") or {}).get(agent_role) or {}
        if ability_state.get("state") != "valid":
            blockers.append(
                {
                    "model": catalog.model_id,
                    "code": "MISSING_ABILITY_EVIDENCE",
                    "reason": ability_state.get("reason") or "ability evidence is not current",
                    "changed_fields": ability_state.get("changed_fields") or [],
                }
            )
            continue
        if role_evidence.get("state") != "valid" or not role_evidence.get("passed"):
            blockers.append(
                {
                    "model": catalog.model_id,
                    "code": "ROLE_QUALIFICATION_FAILED",
                    "role": agent_role,
                    "score": role_evidence.get("score"),
                    "reason": "current role evidence is missing, stale, or below threshold",
                }
            )
            continue
        if requires_context and evidence.get("context", {}).get("state") != "valid":
            context_state = evidence.get("context") or {}
            blockers.append(
                {
                    "model": catalog.model_id,
                    "code": "MISSING_CONTEXT_EVIDENCE",
                    "reason": context_state.get("reason") or "context evidence is not current",
                    "changed_fields": context_state.get("changed_fields") or [],
                }
            )
            continue

        cap = (
            await db.execute(
                select(ModelCapabilityProfile).where(
                    ModelCapabilityProfile.model_catalog_id == catalog.id
                )
            )
        ).scalar_one_or_none()
        # v9.7: effective context (measured) beats declared; unknown => reject for key roles
        ctx = None
        if cap is not None:
            if requires_context:
                ctx = cap.effective_context_window
            elif cap.effective_context_window:
                ctx = cap.effective_context_window
            elif cap.declared_context_window:
                ctx = cap.declared_context_window
            elif cap.context_window:
                ctx = cap.context_window

        context_fit = None
        if requires_context:
            context_fit = context_fit_score(ctx, required_context)
            if context_fit is None:
                blockers.append(
                    {
                        "model": catalog.model_id,
                        "code": "CONTEXT_INSUFFICIENT",
                        "context_window": cap.context_window if cap else None,
                        "required_context": required_context,
                    }
                )
                continue

        score_row = (
            await db.execute(
                select(ModelRoleScore).where(
                    ModelRoleScore.model_catalog_id == catalog.id,
                    ModelRoleScore.agent_role == agent_role,
                )
            )
        ).scalar_one_or_none()
        role_quality = score_row.composite_score if score_row else None
        if role_quality is None or role_quality < floor:
            blockers.append(
                {
                    "model": catalog.model_id,
                    "role": agent_role,
                    "code": "MODEL_QUALITY_FLOOR_UNSATISFIED",
                    "score": role_quality,
                    "floor": floor,
                }
            )
            continue  # spec §41: never silently downgrade below the role floor
        if min_quality and role_quality < min_quality:
            blockers.append(
                {
                    "model": catalog.model_id,
                    "role": agent_role,
                    "code": "MODEL_POLICY_QUALITY_UNSATISFIED",
                    "score": role_quality,
                    "floor": min_quality,
                }
            )
            continue

        snap = (
            await db.execute(
                select(ModelHealthSnapshot).where(
                    ModelHealthSnapshot.model_catalog_id == catalog.id
                )
            )
        ).scalar_one_or_none()
        health_status = snap.health_status if snap else "unknown"
        last_probe_at = snap.last_probe_at if snap else None
        if last_probe_at is not None and last_probe_at.tzinfo is None:
            last_probe_at = last_probe_at.replace(tzinfo=timezone.utc)
        fresh_after = datetime.now(timezone.utc) - timedelta(seconds=health_ttl_seconds)
        if last_probe_at is None or last_probe_at < fresh_after:
            blockers.append(
                {
                    "model": catalog.model_id,
                    "code": "MISSING_FRESH_HEALTH",
                    "last_probe_at": last_probe_at.isoformat() if last_probe_at else None,
                    "ttl_seconds": health_ttl_seconds,
                }
            )
            continue
        if health_status == "degraded" and not allow_degraded:
            blockers.append(
                {
                    "model": catalog.model_id,
                    "code": "MODEL_HEALTH_DEGRADED",
                }
            )
            continue
        if min_health and (health_status not in ("healthy", "degraded") or snap.health_score is None):
            blockers.append(
                {
                    "model": catalog.model_id,
                    "role": agent_role,
                    "code": "MODEL_POLICY_HEALTH_UNSATISFIED",
                    "health_status": health_status,
                    "health_score": snap.health_score if snap else None,
                    "floor": min_health,
                }
            )
            continue
        if health_status not in ("healthy", "degraded"):
            blockers.append(
                {
                    "model": catalog.model_id,
                    "code": "MODEL_HEALTH_UNAVAILABLE",
                    "health_status": health_status,
                }
            )
            continue
        reliability = (snap.success_rate_15m or 0) * 100 if snap and snap.success_rate_15m is not None else None
        health_score = snap.health_score if snap.health_score is not None else (100 if health_status == "healthy" else 70)
        performance_score = await compute_performance_score(db, catalog.id)

        # spec §36 + v9.6 §56 FinalScore
        route_score = (
            weights["quality"] * (role_quality or 0)
            + weights["reliability"] * (reliability or 50)
            + weights["context"] * (context_fit or 0)
            + weights["health"] * health_score
            + weights["performance"] * (performance_score or 50)
        )
        scored.append(
            (
                route_score,
                RouteTarget(
                    provider=catalog.provider,
                    model=catalog.model_id,
                    route_score=round(route_score, 1),
                ),
                {
                    "role_quality": role_quality,
                    "reliability": reliability,
                    "context_fit": context_fit,
                    "health": health_score,
                },
            )
        )

    scored.sort(key=lambda x: -x[0])

    # locked manual primary (spec §122/§80): auto router never overrides
    has_lock = False
    if locked_primary and locked_primary.get("model"):
        locked = next(
            (s for s in scored if s[1].model == locked_primary["model"] and s[1].provider == locked_primary.get("provider")),
            None,
        )
        if locked:
            scored = [locked] + [s for s in scored if s[1] is not locked[1]]
            has_lock = True
        else:
            return RoleRouteResult(assignment=None, blockers=[
                {"role": agent_role, "code": "MODEL_QUALITY_FLOOR_UNSATISFIED", "reason": "locked primary not eligible"}
            ])

    if not scored:
        return RoleRouteResult(
            assignment=None,
            blockers=(blockers or []) + [
                {"role": agent_role, "code": "NO_ELIGIBLE_MODEL", "required_context": required_context}
            ],
        )

    if not has_lock and scored[0][0] < floor:
        return RoleRouteResult(assignment=None, blockers=[
            {
                "role": agent_role,
                "code": "MODEL_QUALITY_FLOOR_UNSATISFIED",
                "required_context": required_context,
                "best_score": scored[0][0],
                "floor": floor,
            }
        ])

    primary_score, primary, detail = scored[0]
    primary_reason = {
        **detail,
        "route_score": primary.route_score,
        "reason": f"RoleQuality {detail['role_quality']}, Reliability {detail['reliability']}, Context Fit {detail['context_fit']}, Health {detail['health']}",
    }

    fallbacks: list[dict] = []
    used_providers = {primary.provider}
    for score, target, d in scored[1:]:
        if len(fallbacks) >= fallback_count:
            break
        if require_diversity and target.provider in used_providers:
            continue
        fallbacks.append(
            {
                "provider": target.provider,
                "model": target.model,
                "route_score": target.route_score,
                "reason": "Provider不同" if require_diversity else "RouteScore高",
            }
        )
        used_providers.add(target.provider)

    assignment = {
        "primary": {
            "provider": primary.provider,
            "model": primary.model,
            "route_score": primary.route_score,
            **primary_reason,
        },
        "fallbacks": fallbacks,
    }
    return RoleRouteResult(
        assignment=assignment,
        blockers=None,
        candidates=[item[1] for item in scored],
    )


def default_policy_for(mode: str = "hybrid", **overrides) -> dict:
    policy = {
        "mode": mode if mode in ("manual", "auto", "hybrid") else "hybrid",
        "min_quality_score": 0.0,
        "min_health_score": 0.0,
        "require_provider_diversity": True,
        "fallback_count": 2,
        "allow_degraded": False,
        "quality_weight": DEFAULT_WEIGHTS["quality"],
        "reliability_weight": DEFAULT_WEIGHTS["reliability"],
        "context_weight": DEFAULT_WEIGHTS["context"],
        "health_weight": DEFAULT_WEIGHTS["health"],
        "performance_weight": DEFAULT_WEIGHTS["performance"],
        "latency_weight": 0.0,
        "cost_weight": 0.0,
        "role_overrides": {},
    }
    policy.update(overrides)
    return policy


async def policy_from_db(db: AsyncSession, policy_id: UUID | None = None) -> dict:
    if policy_id:
        row = (
            await db.execute(
                select(ModelRoutingPolicy).where(ModelRoutingPolicy.id == policy_id)
            )
        ).scalar_one_or_none()
        if row is not None:
            return {
                "mode": row.mode,
                "min_quality_score": row.min_quality_score,
                "min_health_score": row.min_health_score,
                "require_provider_diversity": row.require_provider_diversity,
                "fallback_count": row.fallback_count,
                "allow_degraded": row.allow_degraded,
                "quality_weight": row.quality_weight,
                "reliability_weight": row.reliability_weight,
                "context_weight": row.context_weight,
                "health_weight": row.health_weight,
                "latency_weight": row.latency_weight,
                "cost_weight": row.cost_weight,
                "role_overrides": row.role_overrides_json or {},
            }
    return default_policy_for()
