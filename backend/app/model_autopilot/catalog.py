"""Model autopilot: catalog discovery & sync (spec PR-02/§6, §58–§59).

Syncs provider /models into model_catalog with new/missing/reappeared diff.
New models: availability unknown, auto_route_enabled=false until probe passes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model_autopilot.seed import seed_for_model, static_quality_score_for
from app.models import ModelCapabilityProfile, ModelCatalog

logger = logging.getLogger("novelforge.model_autopilot.catalog")


async def _get_provider_models(base_url: str, api_key: str) -> list[dict]:
    """GET {base}/models — returns raw model objects or raises."""
    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=10)) as client:
        resp = await client.get(
            f"{base_url.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data") or data.get("models") or []
        return list(items)


async def sync_catalog_from_provider(
    db: AsyncSession,
    *,
    provider: str,
    base_url: str,
    api_key: str,
) -> dict:
    """Diff provider /models against catalog. Returns counts (spec §58)."""
    result = {"new": 0, "missing": 0, "reappeared": 0, "unchanged": 0, "total": 0}
    try:
        items = await _get_provider_models(base_url, api_key)
    except Exception as e:  # noqa: BLE001 - report provider failure, don't crash
        logger.warning("catalog sync failed for %s: %s", provider, e)
        return result

    now = datetime.now(timezone.utc)
    seen_ids = [str(i.get("id") or i.get("model") or i.get("name")) for i in items]

    catalog_rows = (
        (await db.execute(select(ModelCatalog).where(ModelCatalog.provider == provider)))
        .scalars()
        .all()
    )
    by_model = {c.model_id: c for c in catalog_rows}

    for item in items:
        model_id = str(item.get("id") or item.get("model") or item.get("name"))
        if not model_id:
            continue
        existing = by_model.get(model_id)
        if existing is None:
            # spec §58: brand-new models must NOT enter auto-route… except
            # models the capability seed already knows (static quality tier
            # exists — reviewed knowledge, not a blind name guess).
            seeded = seed_for_model(model_id) is not None
            catalog = ModelCatalog(
                id=uuid4(),
                provider=provider,
                model_id=model_id,
                display_name=item.get("display_name"),
                availability_status="available",
                discovery_source="provider_api",
                auto_route_enabled=seeded,
                metadata_json=item,
            )
            db.add(catalog)
            await db.flush()
            await ensure_capability_for_catalog(db, catalog)
            result["new"] += 1
        else:
            if existing.availability_status in ("missing", "disabled"):
                existing.availability_status = "available"
                result["reappeared"] += 1
            else:
                result["unchanged"] += 1
            # pre-existing catalog rows (synced before the seed-enable change)
            # get promoted too when the seed knows the model — spec §58 only
            # protects unknown models from auto-route.
            if not existing.auto_route_enabled and seed_for_model(model_id) is not None:
                existing.auto_route_enabled = True
            existing.last_seen_at = now

    for catalog in catalog_rows:
        if catalog.model_id in seen_ids:
            continue
        if catalog.availability_status in ("missing", "disabled"):
            continue
        # Mark consistently-absent models; real usage still works (manual binds)
        catalog.availability_status = "missing"
        result["missing"] += 1

    result["total"] = len(items)
    return result


async def ensure_capability_for_catalog(db: AsyncSession, catalog: ModelCatalog) -> ModelCapabilityProfile:
    """Attach a capability profile from seed/family knowledge (spec §9–§10)."""
    existing = (
        await db.execute(
            select(ModelCapabilityProfile).where(
                ModelCapabilityProfile.model_catalog_id == catalog.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    seed = seed_for_model(catalog.model_id)
    profile = ModelCapabilityProfile(
        id=uuid4(),
        model_catalog_id=catalog.id,
        capability_source="seed" if seed else "unknown",
    )
    if seed:
        profile.context_window = seed.get("context_window")
        profile.max_output_tokens = seed.get("max_output_tokens")
        profile.supports_stream = seed.get("supports_stream", True)
        profile.supports_json_schema = seed.get("supports_json_schema", False)
        profile.supports_tools = seed.get("supports_tools", False)
        profile.supports_reasoning = seed.get("supports_reasoning", False)
        profile.quality_tier = seed.get("quality_tier", "unknown")
        profile.static_quality_score = static_quality_score_for(catalog.model_id)
        profile.capability_confidence = 0.7
    db.add(profile)
    await db.flush()
    return profile
