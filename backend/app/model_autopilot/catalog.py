"""Model autopilot: catalog discovery & sync (spec PR-02/§6, §58–§59).

Syncs provider /models into model_catalog with new/missing/reappeared diff.
New models: availability unknown, auto_route_enabled=false until probe passes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model_autopilot.seed import seed_for_model, static_quality_score_for
from app.models import ModelCapabilityProfile, ModelCatalog

logger = logging.getLogger("novelforge.model_autopilot.catalog")

_SENSITIVE_METADATA_PARTS = ("api_key", "apikey", "token", "secret", "password", "authorization")
_CONTEXT_METADATA_KEYS = (
    "context_window",
    "context_length",
    "max_context_tokens",
    "max_input_tokens",
)


def _safe_provider_metadata(value):
    """Remove credential-shaped fields before provider data reaches JSONB."""
    if isinstance(value, dict):
        return {
            str(key): _safe_provider_metadata(item)
            for key, item in value.items()
            if not any(part in str(key).casefold() for part in _SENSITIVE_METADATA_PARTS)
        }
    if isinstance(value, list):
        return [_safe_provider_metadata(item) for item in value]
    return value


def provider_declared_context_window(metadata: dict | None) -> int | None:
    """Extract a provider-declared context size without guessing by name."""

    root = metadata or {}
    candidates = [root]
    for key in ("capabilities", "limits", "metadata"):
        nested = root.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for container in candidates:
        for key in _CONTEXT_METADATA_KEYS:
            value = container.get(key)
            if isinstance(value, bool) or value is None:
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if 4_096 <= parsed <= 4_000_000:
                return parsed
    return None


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
    seen_ids = [
        str(raw_id)
        for item in items
        if (raw_id := item.get("id") or item.get("model") or item.get("name"))
    ]

    catalog_rows = (
        (await db.execute(select(ModelCatalog).where(ModelCatalog.provider == provider)))
        .scalars()
        .all()
    )
    by_model = {c.model_id: c for c in catalog_rows}

    from app.model_autopilot.classification import classify_catalog_model

    for item in items:
        raw_model_id = item.get("id") or item.get("model") or item.get("name")
        if not raw_model_id:
            continue
        model_id = str(raw_model_id)
        safe_item = _safe_provider_metadata(item)
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
                metadata_json=safe_item,
            )
            classify_catalog_model(catalog)
            db.add(catalog)
            await db.flush()
            # v9.8 (P0-3): compute & store the SAFE endpoint identity hash from
            # the real normalized base URL / routing endpoint (never an optional
            # metadata field that is never auto-filled; never a secret).
            await _refresh_endpoint_identity(db, catalog, base_url=base_url)
            await ensure_capability_for_catalog(db, catalog)
            result["new"] += 1
        else:
            if existing.availability_status in ("missing", "disabled"):
                existing.availability_status = "available"
                result["reappeared"] += 1
            else:
                result["unchanged"] += 1
            classify_catalog_model(existing)
            # pre-existing catalog rows (synced before the seed-enable change)
            # get promoted too when the seed knows the model — spec §58 only
            # protects unknown models from auto-route.
            if not existing.auto_route_enabled and seed_for_model(model_id) is not None:
                existing.auto_route_enabled = True
            existing.last_seen_at = now
            existing.metadata_json = {
                **_safe_provider_metadata(dict(existing.metadata_json or {})),
                **safe_item,
            }
            # v9.8 (P0-3): refresh the SAFE endpoint identity hash on every sync
            # so a base-url / routing-endpoint change is captured; an API-key
            # change does NOT alter the hash.
            await _refresh_endpoint_identity(db, existing, base_url=base_url)
            await ensure_capability_for_catalog(db, existing)

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
    provider_context = provider_declared_context_window(catalog.metadata_json)
    existing = (
        await db.execute(
            select(ModelCapabilityProfile).where(
                ModelCapabilityProfile.model_catalog_id == catalog.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if provider_context is not None and existing.declared_context_window is None:
            existing.declared_context_window = provider_context
            if existing.context_window is None:
                existing.context_window = provider_context
            if existing.capability_source in (None, "unknown"):
                existing.capability_source = "provider_metadata"
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
    elif provider_context is not None:
        profile.context_window = provider_context
        profile.declared_context_window = provider_context
        profile.capability_source = "provider_metadata"
        profile.capability_confidence = 0.8
    db.add(profile)
    await db.flush()
    return profile


async def _refresh_endpoint_identity(
    db: AsyncSession,
    catalog: ModelCatalog,
    *,
    base_url: str | None = None,
) -> None:
    """Compute and persist the SAFE endpoint identity hash for one catalog.

    Uses the real normalized provider base URL / routing endpoint (task裁定 #8).
    API-key changes do NOT change the hash; base-URL / routing-endpoint changes
    DO. The result is stored in catalog.metadata_json["endpoint_identity_hash"]
    so it is auditable and stable. No secret is persisted.
    """
    del db
    from app.model_eval.evidence import (
        compute_endpoint_identity_hash,
        compute_upstream_identity_hash,
        normalize_endpoint,
    )

    meta = dict(catalog.metadata_json or {})
    effective_base_url = base_url or meta.get("base_url") or meta.get("provider_base_url")
    routing = meta.get("routing_endpoint")
    eh = compute_endpoint_identity_hash(base_url=effective_base_url, routing_endpoint=routing)
    uh = compute_upstream_identity_hash(
        owned_by=meta.get("owned_by"),
        created=meta.get("created"),
        upstream_revision=meta.get("upstream_revision"),
    )
    catalog.endpoint_identity_hash = eh
    catalog.upstream_identity_hash = uh
    meta["endpoint_identity_hash"] = eh
    meta["upstream_identity_hash"] = uh
    if effective_base_url:
        meta["endpoint_normalized"] = normalize_endpoint(effective_base_url)
    catalog.metadata_json = meta
