"""Model autopilot ARQ jobs: catalog sync + health probes (spec §25–§28)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import async_session_factory
from app.model_autopilot.catalog import sync_catalog_from_provider
from app.model_autopilot.health import upsert_health_snapshot
from app.model_autopilot.probe import probe_model_ping
from app.models import (
    ModelCatalog,
    ModelHealthProbe,
    ModelHealthSnapshot,
    ModelRoutePlan,
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


async def _probe_due(db, catalog: ModelCatalog) -> bool:
    snap = (
        await db.execute(
            select(ModelHealthSnapshot).where(
                ModelHealthSnapshot.model_catalog_id == catalog.id
            )
        )
    ).scalar_one_or_none()
    if snap is None or snap.last_probe_at is None:
        return True
    status = snap.health_status or "unknown"
    interval = PROBE_MINUTES.get(status, 30)
    return snap.last_probe_at <= datetime.now(timezone.utc) - timedelta(minutes=interval)


async def _route_plan_models(db, session: WritingSession) -> list[tuple[str, str]]:
    """(provider, model) pairs referenced by an active session route plan."""
    if not session.model_route_plan_id:
        return []
    plan = (
        await db.execute(
            select(ModelRoutePlan).where(ModelRoutePlan.id == session.model_route_plan_id)
        )
    ).scalar_one_or_none()
    if plan is None:
        return []
    out = []
    for assignment in (plan.assignments_json or {}).values():
        primary = assignment.get("primary") or {}
        if primary.get("model"):
            out.append((primary.get("provider") or "", primary["model"]))
        for fb in assignment.get("fallbacks") or []:
            if fb.get("model"):
                out.append((fb.get("provider") or "", fb["model"]))
    return out


async def model_health_probe_tick(ctx):
    """Cron: probe due models without competing with an active chapter pipeline (spec §27–§28)."""
    report = {"probed": 0, "skipped_active": 0}
    try:
        async with async_session_factory() as db:
            active_session = (
                await db.execute(
                    select(WritingSession.id).where(
                        WritingSession.status.in_(("running", "created", "paused", "waiting_editorial")),
                    )
                )
            ).scalar_one_or_none()

            wait_models: list[tuple[str, str]] = []
            if active_session is not None:
                session = (
                    await db.execute(
                        select(WritingSession).where(WritingSession.id == active_session)
                    )
                ).scalar_one()
                wait_models = await _route_plan_models(db, session)
                if not wait_models:
                    report["skipped_active"] = 1

            catalogs = list((await db.execute(select(ModelCatalog))).scalars().all())
            if wait_models:
                targets = [
                    c for c in catalogs
                    if (c.provider, c.model_id) in wait_models and await _probe_due(db, c)
                ]
            else:
                targets = [c for c in catalogs if await _probe_due(db, c)]
            targets = targets[:6]

            for catalog in targets:
                try:
                    probe = await probe_model_ping(db, catalog)
                    db.add(probe)
                    await upsert_health_snapshot(db, catalog.id)
                    report["probed"] += 1
                except Exception as e:  # noqa: BLE001
                    logger.debug("probe failed %s/%s: %s", catalog.provider, catalog.model_id, e)
            await db.commit()
        if report["probed"]:
            logger.info("model_health_probe_tick: %s", report)
    except Exception as e:  # noqa: BLE001
        logger.warning("model_health_probe_tick failed: %s", e)
        return {"error": str(e)}
    return report
