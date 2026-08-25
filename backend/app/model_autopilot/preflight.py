"""Model autopilot: WritingSession preflight (spec §29–§33, §62–§64).

Session create → catalog sync → probe candidates → capability/context check →
per-role routing → ModelRoutePlan → session running. Failures block the
session with stop_reason=model_preflight_failed.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model_autopilot.capability import DEFAULT_ROLE_QUALITY_FLOOR, required_context_for
from app.model_autopilot.catalog import ensure_capability_for_catalog, sync_catalog_from_provider
from app.model_autopilot.health import upsert_health_snapshot
from app.model_autopilot.probe import probe_model_ping
from app.model_autopilot.router import build_role_route, policy_from_db
from app.model_autopilot.scoring import compute_role_score
from app.models import (
    AgentModelBinding,
    ModelCatalog,
    ModelRoutePlan,
    WritingSession,
)

logger = logging.getLogger("novelforge.model_autopilot.preflight")

# Roles routed during preflight (spec §64/§42).
PREFLIGHT_ROLES = [
    "chapter_planner",
    "draft_writer",
    "review_agent",
    "state_extractor",
    "style_analyzer",
]

# Context estimates per role (defaults; real estimates could come from the ctx assembler).
ROLE_CONTEXT_ESTIMATE = {
    "chapter_planner": (40000, 8000),
    "draft_writer": (90000, 12000),
    "review_agent": (60000, 10000),
    "state_extractor": (40000, 8000),
    "style_analyzer": (30000, 6000),
}

UNKNOWN = "unknown"


def _providers_from_env() -> list[tuple[str, str, str]]:
    """(provider, base_url, api_key) from environment; dedupe by provider name."""
    out = {}
    provider_env = [
        ("PRIMARY_BASE_URL", "PRIMARY_API_KEY", "primary"),
        ("FALLBACK_BASE_URL", "FALLBACK_API_KEY", "fallback"),
        ("NEW_API_BASE_URL", "NEW_API_API_KEY", "new-api"),
        ("OPENROUTER_BASE_URL", "OPENROUTER_API_KEY", "openrouter"),
    ]
    for url_key, key_key, name in provider_env:
        url = os.environ.get(url_key)
        key = os.environ.get(key_key)
        if url and key:
            out[name] = (name, url, key)
    return list(out.values())


async def _provider_sync_list(db: AsyncSession) -> list[tuple[str, str, str]]:
    env_providers = _providers_from_env()
    bound = (await db.execute(select(AgentModelBinding.provider).distinct())).scalars().all()
    existing = {p[0] for p in env_providers}
    for provider in bound:
        if provider in existing:
            continue
        p = provider.upper().replace("-", "_")
        env_providers.append(
            (provider, os.environ.get(f"{p}_BASE_URL") or os.environ.get("PRIMARY_BASE_URL"), os.environ.get(f"{p}_API_KEY") or os.environ.get("PRIMARY_API_KEY"))
        )
    return env_providers


async def bootstrap_catalog_and_probes() -> dict:
    """Network steps (sync + probe) in their OWN transactions (spec §30 steps 1–3).

    Runs without holding any session/book locks so an in-flight advance job is
    never blocked on provider IO; the DB-only core then runs in the caller txn.
    """
    from app.database import async_session_factory

    report = {"providers": 0, "probed": 0}
    for provider, base_url, api_key in await _provider_sync_list_for_network():
        try:
            async with async_session_factory() as db:
                await sync_catalog_from_provider(
                    db, provider=provider, base_url=base_url or "", api_key=api_key or ""
                )
                await db.commit()
                report["providers"] += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("catalog sync skipped for %s: %s", provider, e)
            await db.rollback()
    # probe eligible candidates (L1 ping, limited). Deadlock fix: probe
    # auto-route models AND seed-known ones — otherwise a fresh catalog has
    # zero candidates and zero probes forever (spec §30 step 3).
    catalogs = list(
        (
            await db.execute(
                select(ModelCatalog).where(ModelCatalog.enabled.is_(True), ModelCatalog.availability_status == "available")
            )
        )
        .scalars()
        .all()
    )
    from app.model_autopilot.seed import seed_for_model

    probe_targets = [
        c for c in catalogs
        if c.auto_route_enabled or seed_for_model(c.model_id) is not None
    ][:6]
    for catalog in probe_targets:
        try:
            probe = await probe_model_ping(db, catalog)
            db.add(probe)
            await upsert_health_snapshot(db, catalog.id)
            report["probed"] += 1
        except Exception as e:  # noqa: BLE001
            logger.debug("probe skipped %s/%s: %s", catalog.provider, catalog.model_id, e)
    await db.commit()
    return report


async def _provider_sync_list_for_network() -> list[tuple[str, str, str]]:
    """Env-based provider list without a DB round-trip (used pre-catalog)."""
    return _providers_from_env()


async def run_model_preflight(
    db: AsyncSession,
    *,
    session: WritingSession,
    binding: AgentModelBinding | None = None,
) -> dict:
    """Execute the DB-only core of preflight inside the caller's transaction (spec §30 steps 4–10)."""
    now = datetime.now(timezone.utc)
    policy = await policy_from_db(db, binding.routing_policy_id if binding else None)

    catalog_rows = list(
        (
            await db.execute(select(ModelCatalog).where(ModelCatalog.auto_route_enabled.is_(True)))
        )
        .scalars()
        .all()
    )

    # 4-7. per-role scoring + context + routing
    for catalog in catalog_rows:
        await ensure_capability_for_catalog(db, catalog)

    roles_result = {}
    blockers = []
    for role in PREFLIGHT_ROLES:
        est_in, est_out = ROLE_CONTEXT_ESTIMATE.get(role, (50000, 8000))
        required_ctx = required_context_for(est_in, est_out, 0)
        result = await build_role_route(
            db,
            agent_role=role,
            required_context=required_ctx,
            policy=policy,
            allowed_ids=(binding.allowed_model_ids if binding else None),
            blocked_ids=(binding.blocked_model_ids if binding else None),
            locked_primary=(
                {"model": binding.primary_model, "provider": binding.provider} if binding and binding.manual_primary_locked else None
            ),
        )
        role_floor = DEFAULT_ROLE_QUALITY_FLOOR.get(role, 70)
        roles_result[role] = {
            "required_context": required_ctx,
            "primary": result.assignment["primary"] if result.assignment else None,
            "fallbacks": result.assignment["fallbacks"] if result.assignment else [],
            "minimum_quality_score": role_floor,
        }
        if result.blockers:
            blockers.extend(result.blockers)

    if blockers:
        session.status = "blocked"
        session.stop_reason = "model_preflight_failed"
        session.stop_detail = {"blockers": blockers}
        session.model_preflight_status = "blocked"
        session.model_preflight_detail = {"blockers": blockers, "roles": roles_result}
        return {"status": "blocked", "blockers": blockers, "roles": roles_result}

    # 8-9. persist ModelRoutePlan
    plan = ModelRoutePlan(
        id=uuid.uuid4(),
        book_id=session.book_id,
        writing_session_id=session.id,
        plan_version=1,
        generated_at=now,
        valid_until=now + timedelta(hours=6),
        policy_id=(binding.routing_policy_id if binding else None),
        policy_version=1,
        assignments_json=roles_result,
        health_snapshot_at=now,
        reason_json={"mode": policy.get("mode", "hybrid")},
        status="active",
    )
    db.add(plan)
    await db.flush()
    session.model_route_plan_id = plan.id
    session.model_routing_policy_version = 1
    session.model_preflight_status = "pass"
    session.model_preflight_detail = {"roles": roles_result, "plan_id": str(plan.id)}
    return {"status": "pass", "route_plan_id": str(plan.id), "roles": roles_result, "warnings": []}


async def compute_route_plan_scores(db: AsyncSession, plan: ModelRoutePlan) -> None:
    """Backfill per-role role-score snapshots after plan creation."""
    for role, assignment in (plan.assignments_json or {}).items():
        primary = assignment.get("primary") or {}
        if not primary.get("model"):
            continue
        catalog = (
            await db.execute(
                select(ModelCatalog).where(
                    ModelCatalog.provider == primary.get("provider"),
                    ModelCatalog.model_id == primary.get("model"),
                )
            )
        ).scalar_one_or_none()
        if catalog:
            await compute_role_score(db, catalog, role)
