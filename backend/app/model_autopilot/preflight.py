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

from app.agents.registry import ROLE_REGISTRY, required_roles
from app.model_autopilot.capability import DEFAULT_ROLE_QUALITY_FLOOR, required_context_for
from app.model_autopilot.catalog import ensure_capability_for_catalog, sync_catalog_from_provider
from app.model_autopilot.health import upsert_health_snapshot
from app.model_autopilot.probe import probe_model_ping
from app.model_autopilot.router import build_role_route, policy_from_db
from app.model_autopilot.scoring import compute_role_score
from app.v74_utils import ModelBindingService
from app.models import (
    AgentModelBinding,
    ModelCatalog,
    ModelHealthSnapshot,
    ModelRoutePlan,
    WritingSession,
)

logger = logging.getLogger("novelforge.model_autopilot.preflight")

# Roles routed during preflight (spec §64/§42).
PREFLIGHT_ROLES = required_roles()

# Context estimates per role (defaults; real estimates could come from the ctx assembler).
ROLE_CONTEXT_ESTIMATE = {
    "chapter_planner": (40000, 8000),
    "draft_writer": (90000, 12000),
    "review_agent": (60000, 10000),
    "state_extractor": (40000, 8000),
    "style_analyzer": (30000, 6000),
    "outline_parser": (70000, 10000),
    "blank_planner": (60000, 10000),
    "local_rewrite_editor": (20000, 6000),
    "drift_audit": (80000, 10000),
    "query_planner": (40000, 8000),
    "evidence_ranker": (35000, 8000),
    "memory_compiler": (44000, 8000),
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

    report = {
        "providers": 0,
        "probed": 0,
        "skipped_fresh": 0,
        "promoted_text": 0,
        "configured_models": 0,
        "errors": [],
    }
    # Include logical providers from persisted bindings.  Production bindings
    # commonly use ``new-api`` while PRIMARY_* is the credential source; env-
    # only discovery would otherwise create a duplicate ``primary`` catalog
    # and leave the approved binding without a catalog row.
    async with async_session_factory() as db:
        providers = await _provider_sync_list(db)

    for provider, base_url, api_key in providers:
        if not base_url or not api_key:
            report["errors"].append(
                {
                    "provider": provider,
                    "phase": "catalog_sync",
                    "error": "provider_credentials_missing",
                }
            )
            continue
        try:
            async with async_session_factory() as db:
                await sync_catalog_from_provider(
                    db, provider=provider, base_url=base_url or "", api_key=api_key or ""
                )
                await db.commit()
                report["providers"] += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("catalog sync skipped for %s: %s", provider, e)
            report["errors"].append({"provider": provider, "phase": "catalog_sync", "error": type(e).__name__})

    # Probe every explicitly configured model first, even when /models omitted
    # modality metadata.  A successful non-empty text ping is the runtime
    # handshake that may promote only that approved model.  Unknown unrelated
    # catalog entries remain excluded from all text evaluation.
    ttl_seconds = int(os.environ.get("MODEL_PREWRITE_HEALTH_TTL_SECONDS", "300"))
    probe_limit = int(os.environ.get("MODEL_PREFLIGHT_PROBE_LIMIT", "12"))
    fresh_after = datetime.now(timezone.utc) - timedelta(seconds=ttl_seconds)
    async with async_session_factory() as db:
        bindings = list(
            (await db.execute(select(AgentModelBinding))).scalars().all()
        )
        configured_keys = {
            (binding.provider, model)
            for binding in bindings
            if binding.agent_role in PREFLIGHT_ROLES
            for model in (binding.primary_model, binding.fallback_model)
            if model
        }
        report["configured_models"] = len(configured_keys)
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
        by_key = {(catalog.provider, catalog.model_id): catalog for catalog in catalogs}
        configured_catalogs = [
            by_key[key] for key in sorted(configured_keys) if key in by_key
        ]
        for provider, model in sorted(configured_keys - set(by_key)):
            report["errors"].append(
                {
                    "provider": provider,
                    "model": model,
                    "phase": "configured_catalog",
                    "error": "configured_model_not_discovered",
                }
            )

        configured_ids = {catalog.id for catalog in configured_catalogs}
        other_candidates = sorted(
            (
                catalog
                for catalog in catalogs
                if catalog.id not in configured_ids
                and catalog.text_generation_eligible
                and catalog.auto_route_enabled
            ),
            key=lambda catalog: (catalog.provider, catalog.model_id),
        )
        remaining = max(0, probe_limit - len(configured_catalogs))
        candidates = [
            *((catalog, True) for catalog in configured_catalogs),
            *((catalog, False) for catalog in other_candidates[:remaining]),
        ]

        from app.model_autopilot.catalog import ensure_capability_for_catalog
        from app.model_autopilot.classification import promote_configured_text_model

        for catalog, configured in candidates:
            snapshot = (
                await db.execute(
                    select(ModelHealthSnapshot).where(
                        ModelHealthSnapshot.model_catalog_id == catalog.id
                    )
                )
            ).scalar_one_or_none()
            last_probe = snapshot.last_probe_at if snapshot else None
            if last_probe is not None and last_probe.tzinfo is None:
                last_probe = last_probe.replace(tzinfo=timezone.utc)
            needs_handshake = configured and (
                not catalog.text_generation_eligible or not catalog.auto_route_enabled
            )
            if (
                not needs_handshake
                and last_probe is not None
                and last_probe >= fresh_after
            ):
                report["skipped_fresh"] += 1
                continue
            try:
                probe = await probe_model_ping(
                    db,
                    catalog,
                    allow_reasoning_retry=configured,
                )
                db.add(probe)
                if configured and probe.status == "ok" and probe.output_valid:
                    if not catalog.text_generation_eligible:
                        promote_configured_text_model(catalog)
                        report["promoted_text"] += 1
                    else:
                        catalog.auto_route_enabled = True
                    await ensure_capability_for_catalog(db, catalog)
                await db.flush()
                await upsert_health_snapshot(db, catalog.id)
                await db.commit()
                report["probed"] += 1
                if configured and (probe.status != "ok" or not probe.output_valid):
                    report["errors"].append(
                        {
                            "provider": catalog.provider,
                            "model": catalog.model_id,
                            "phase": "configured_text_handshake",
                            "error": probe.error_code or "empty_text_output",
                        }
                    )
            except Exception as e:  # noqa: BLE001
                await db.rollback()
                logger.warning("prewrite probe failed %s/%s: %s", catalog.provider, catalog.model_id, e)
                report["errors"].append(
                    {
                        "provider": catalog.provider,
                        "model": catalog.model_id,
                        "phase": "l1_ping",
                        "error": type(e).__name__,
                    }
                )
    return report


async def run_model_preflight(
    db: AsyncSession,
    *,
    session: WritingSession,
    binding: AgentModelBinding | None = None,
) -> dict:
    """Execute the DB-only core of preflight inside the caller's transaction (spec §30 steps 4–10)."""
    now = datetime.now(timezone.utc)
    from app.model_eval.engine import ensure_v98_suites

    await ensure_v98_suites(db)
    await db.flush()
    default_policy = await policy_from_db(db, binding.routing_policy_id if binding else None)

    catalog_rows = list(
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

    # 4-7. per-role scoring + context + routing
    for catalog in catalog_rows:
        await ensure_capability_for_catalog(db, catalog)
        # role scores MUST exist before routing, otherwise every candidate is
        # filtered out as "no score" (spec §30 step 6; second deadlock)
        for role in PREFLIGHT_ROLES:
            await compute_role_score(db, catalog, role)

    binding_service = ModelBindingService(db)
    catalog_id_by_target = {
        (catalog.provider, catalog.model_id): str(catalog.id) for catalog in catalog_rows
    }
    policy_cache = {
        getattr(binding, "routing_policy_id", None): default_policy,
    }

    roles_result = {}
    blockers = []
    for role in PREFLIGHT_ROLES:
        # Binding constraints are role-scoped.  Applying the draft-writer
        # allowlist/manual lock to every role can silently remove the model
        # explicitly selected for planner/reviewer/etc. and deadlock startup.
        role_binding = await binding_service.get_binding(role, getattr(session, "book_id", None))
        if role_binding is None and binding is not None and getattr(binding, "agent_role", None) == role:
            role_binding = binding

        policy_id = getattr(role_binding, "routing_policy_id", None)
        if policy_id not in policy_cache:
            policy_cache[policy_id] = await policy_from_db(db, policy_id)
        role_policy = policy_cache[policy_id]

        allowed_ids = list(getattr(role_binding, "allowed_model_ids", None) or [])
        blocked_ids = list(getattr(role_binding, "blocked_model_ids", None) or [])
        if role_binding is not None and allowed_ids:
            # Production release may replace the explicit primary while a
            # historical candidate allowlist remains.  The chosen primary is
            # part of its own role's candidate set unless explicitly blocked.
            primary_catalog_id = catalog_id_by_target.get(
                (role_binding.provider, role_binding.primary_model)
            )
            if (
                primary_catalog_id
                and primary_catalog_id not in allowed_ids
                and primary_catalog_id not in blocked_ids
            ):
                allowed_ids.append(primary_catalog_id)

        expected = ROLE_REGISTRY[role].expected_context_tokens
        est_in, est_out = ROLE_CONTEXT_ESTIMATE.get(
            role,
            (max(8000, int(expected * 0.75)), max(4000, int(expected * 0.10))),
        )
        required_ctx = required_context_for(est_in, est_out, 0)
        result = await build_role_route(
            db,
            agent_role=role,
            required_context=required_ctx,
            policy=role_policy,
            allowed_ids=allowed_ids or None,
            blocked_ids=blocked_ids or None,
            locked_primary=(
                {"model": role_binding.primary_model, "provider": role_binding.provider}
                if role_binding and role_binding.manual_primary_locked
                else None
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
        logger.warning(
            "model preflight blocked session=%s blockers=%s",
            getattr(session, "id", None),
            blockers,
        )
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
        reason_json={"mode": default_policy.get("mode", "hybrid")},
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
