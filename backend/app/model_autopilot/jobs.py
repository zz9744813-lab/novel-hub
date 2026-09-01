"""Model autopilot ARQ jobs: catalog sync + health probes (spec §25–§28)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import async_session_factory
from app.model_autopilot.catalog import sync_catalog_from_provider
from app.model_autopilot.health import upsert_health_snapshot
from app.model_autopilot.probe import probe_model_ping
from app.models import (
    ModelCatalog,
    ModelHealthSnapshot,
    WritingSession,
)

logger = logging.getLogger("novelforge.model_autopilot.jobs")

# probe interval by priority status (spec §26)
PROBE_MINUTES = {
    "degraded": 5,
    "unknown": 15,
    "healthy": 30,
    "unavailable": 30,
    "unstable": 5,
    "active_primary": 5,
    "active_fallback": 10,
}


async def model_catalog_sync_tick(ctx):
    """Cron: refresh provider /models into the catalog (spec §58)."""
    from app.model_autopilot.preflight import _provider_sync_list

    report = {"providers": 0, "syncs": []}
    try:
        async with async_session_factory() as db:
            for provider, base_url, api_key in await _provider_sync_list(db):
                if not base_url:
                    continue
                result = await sync_catalog_from_provider(
                    db, provider=provider, base_url=base_url, api_key=api_key or ""
                )
                report["syncs"].append({"provider": provider, **result})
                report["providers"] += 1
            await db.commit()
        if report["providers"]:
            logger.info("model_catalog_sync_tick: %s", report)
    except Exception as e:  # noqa: BLE001
        logger.warning("model_catalog_sync_tick failed: %s", e)
        return {"error": str(e)}
    return report


async def _probe_due(
    db, catalog: ModelCatalog, interval_minutes: int | None = None
) -> bool:
    snap = (
        await db.execute(
            select(ModelHealthSnapshot).where(
                ModelHealthSnapshot.model_catalog_id == catalog.id
            )
        )
    ).scalar_one_or_none()
    if snap is None or snap.last_probe_at is None:
        return True
    if interval_minutes is None:
        status = snap.health_status or "unknown"
        interval_minutes = PROBE_MINUTES.get(status, 30)
    last_probe_at = snap.last_probe_at
    if last_probe_at.tzinfo is None:
        last_probe_at = last_probe_at.replace(tzinfo=timezone.utc)
    return last_probe_at <= datetime.now(timezone.utc) - timedelta(minutes=interval_minutes)


async def _route_plan_models_with_kind(db, session: WritingSession) -> list[tuple[str, str, str]]:
    """(provider, model, kind) referenced by an active session route plan."""
    if not session.model_route_plan_id:
        return []
    from app.models import ModelRoutePlan

    plan = (
        await db.execute(
            select(ModelRoutePlan).where(ModelRoutePlan.id == session.model_route_plan_id)
        )
    ).scalar_one_or_none()
    if plan is None:
        return []
    from app.model_autopilot.retired_models import is_retired_production_model

    out = []
    for assignment in (plan.assignments_json or {}).values():
        primary = assignment.get("primary") or {}
        if primary.get("model") and not is_retired_production_model(primary.get("model")):
            out.append((primary.get("provider") or "", primary["model"], "primary"))
        for fb in assignment.get("fallbacks") or []:
            if fb.get("model") and not is_retired_production_model(fb.get("model")):
                out.append((fb.get("provider") or "", fb["model"], "fallback"))
    return out


async def model_health_probe_tick(ctx):
    """Cron: probe due models without competing with an active chapter pipeline (spec §27–§28)."""
    report = {"probed": 0, "skipped_active": 0, "errors": []}
    try:
        async with async_session_factory() as db:
            # v9.6 §54: MULTIPLE books may run sessions at once — aggregate all.
            active_sessions = (
                (
                    await db.execute(
                        select(WritingSession).where(
                            WritingSession.status.in_(
                                ("running", "created", "paused", "waiting_editorial", "blocked")
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            # distinct route models per session (provider, model) with role kind
            route_kind: dict[tuple[str, str], str] = {}
            for session in active_sessions:
                for provider, model, kind in await _route_plan_models_with_kind(db, session):
                    current = route_kind.get((provider, model))
                    if current != "primary":
                        route_kind[(provider, model)] = kind
            if active_sessions and not route_kind:
                report["skipped_active"] = 1

            catalogs = list(
                (
                    await db.execute(
                        select(ModelCatalog).where(
                            ModelCatalog.enabled.is_(True),
                            ModelCatalog.availability_status == "available",
                            ModelCatalog.text_generation_eligible.is_(True),
                        )
                    )
                ).scalars().all()
            )
            catalogs.sort(
                key=lambda catalog: (
                    0 if route_kind.get((catalog.provider, catalog.model_id)) == "primary" else
                    1 if route_kind.get((catalog.provider, catalog.model_id)) == "fallback" else 2,
                    catalog.provider,
                    catalog.model_id,
                )
            )
            targets = []
            for catalog in catalogs:
                kind = route_kind.get((catalog.provider, catalog.model_id))
                if kind == "primary":
                    interval = 5  # spec §26: active primary 5min
                elif kind == "fallback":
                    interval = 10  # active fallback 10min
                else:
                    interval = None
                if await _probe_due(db, catalog, interval_minutes=interval):
                    targets.append(catalog)
            targets = targets[: int(os.environ.get("MODEL_HEALTH_TICK_LIMIT", "12"))]

            # Release the selection transaction before provider I/O.  No
            # session/book row lock is held while the lightweight ping runs.
            await db.commit()

            for catalog in targets:
                try:
                    probe = await probe_model_ping(db, catalog)
                    db.add(probe)
                    await db.flush()
                    await upsert_health_snapshot(db, catalog.id)
                    await db.commit()
                    report["probed"] += 1
                except Exception as e:  # noqa: BLE001
                    await db.rollback()
                    logger.warning("probe failed %s/%s: %s", catalog.provider, catalog.model_id, e)
                    report["errors"].append(
                        {
                            "provider": catalog.provider,
                            "model": catalog.model_id,
                            "error": type(e).__name__,
                        }
                    )
        if report["probed"]:
            logger.info("model_health_probe_tick: %s", report)
    except Exception as e:  # noqa: BLE001
        logger.warning("model_health_probe_tick failed: %s", e)
        return {"error": str(e)}
    return report
