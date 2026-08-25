"""Model autopilot: health aggregation & classification (spec §21, §23–§24, §52).

One snapshot row per model. Production signals weigh 70%, probes 30%.
"""
from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ModelCatalog, ModelHealthProbe, ModelHealthSnapshot

logger = logging.getLogger("novelforge.model_autopilot.health")

AUTH_ERROR_CODES = {"HTTP_401", "HTTP_403", "AUTH_ERROR", "UNAUTHORIZED"}
NOT_FOUND_CODES = {"MODEL_NOT_FOUND", "HTTP_404"}


def _percentile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int((len(ordered) - 1) * p))
    return ordered[idx]


def _window_rate(rows: list[ModelHealthProbe], cutoff: datetime) -> float | None:
    window = [r for r in rows if r.started_at and r.started_at >= cutoff]
    if not window:
        return None
    return sum(1 for r in window if r.status == "ok") / len(window)


def classify_health(
    *,
    probe_ok_recent: float | None,
    prod_15m: float | None,
    consecutive_failures: int,
    last_probe_status: str | None,
    last_error: str | None,
    has_valid_probe: bool,
) -> str:
    """Spec §24 classification."""
    if not has_valid_probe and prod_15m is None:
        return "unknown"
    if last_error in AUTH_ERROR_CODES | NOT_FOUND_CODES:
        return "unavailable"
    if consecutive_failures >= 3:
        return "unavailable"
    if prod_15m is not None:
        if prod_15m >= 0.95 and (probe_ok_recent is None or probe_ok_recent >= 0.5):
            return "healthy"
        if prod_15m >= 0.70:
            return "degraded"
        if prod_15m > 0:
            return "unstable"
        return "unavailable"
    return "unknown"


async def upsert_health_snapshot(db: AsyncSession, catalog_id: uuid.UUID) -> ModelHealthSnapshot:
    """Recompute one model's snapshot from recent probes + production signals."""
    now = datetime.now(timezone.utc)
    rows = list(
        (
            await db.execute(
                select(ModelHealthProbe)
                .where(ModelHealthProbe.model_catalog_id == catalog_id)
                .order_by(ModelHealthProbe.started_at.desc())
            )
        ).scalars()
    )[:400]  # bounded window

    prod_rows = [r for r in rows if r.probe_type == "production"]
    ping_rows = [r for r in rows if r.probe_type == "l1_ping" and r.latency_ms]
    l1_ok = [r for r in rows if r.probe_type == "l1_ping"]

    snap = (
        await db.execute(
            select(ModelHealthSnapshot).where(
                ModelHealthSnapshot.model_catalog_id == catalog_id
            )
        )
    ).scalar_one_or_none()
    if snap is None:
        snap = ModelHealthSnapshot(
            id=uuid.uuid4(), model_catalog_id=catalog_id, health_status="unknown"
        )
        db.add(snap)
        await db.flush()

    # per-window success rates (production signals first; probes supplement).
    # v9.6 §52: a real 0% production rate must NOT fall back to probes.
    snap.success_rate_15m = _window_rate(prod_rows, now - timedelta(minutes=15))
    if snap.success_rate_15m is None:
        snap.success_rate_15m = _window_rate(l1_ok, now - timedelta(minutes=15))
    prod_1h = _window_rate(prod_rows, now - timedelta(hours=1))
    snap.success_rate_1h = prod_1h if prod_1h is not None else _window_rate(l1_ok, now - timedelta(hours=1))
    prod_24h = _window_rate(prod_rows, now - timedelta(hours=24))
    snap.success_rate_24h = prod_24h if prod_24h is not None else _window_rate(l1_ok, now - timedelta(hours=24))

    snap.p50_latency_ms = _percentile([r.latency_ms for r in ping_rows], 0.5)
    snap.p95_latency_ms = _percentile([r.latency_ms for r in ping_rows], 0.95)

    snap.consecutive_failures = 0
    if prod_rows:
        for r in prod_rows[:20]:
            if r.status == "ok":
                break
            snap.consecutive_failures += 1
        last_ok = next((r for r in prod_rows if r.status == "ok"), None)
        last_fail = next((r for r in prod_rows if r.status != "ok"), None)
        snap.last_success_at = last_ok.started_at if last_ok else None
        snap.last_failure_at = last_fail.started_at if last_fail else None
    if l1_ok:
        snap.last_probe_at = l1_ok[0].started_at

    error_mix = Counter(r.error_code for r in rows if r.status != "ok" and r.error_code)
    snap.error_mix_json = dict(error_mix.most_common(8))

    recent_probe_ok = None
    if l1_ok[:3]:
        recent_probe_ok = sum(1 for r in l1_ok[:3] if r.status == "ok") / 3

    last_probe_status = l1_ok[0].status if l1_ok else None
    last_error = l1_ok[0].error_code if l1_ok and l1_ok[0].status != "ok" else None

    snap.health_status = classify_health(
        probe_ok_recent=recent_probe_ok,
        prod_15m=snap.success_rate_15m,
        consecutive_failures=snap.consecutive_failures,
        last_probe_status=last_probe_status,
        last_error=last_error,
        has_valid_probe=bool(l1_ok),
    )

    # health_score: production 70% + probe 30% (spec §52)
    probe_rate = _window_rate(l1_ok, now - timedelta(minutes=15))
    if snap.success_rate_15m is not None:
        snap.health_score = round(0.7 * snap.success_rate_15m * 100 + 0.3 * (probe_rate or 0) * 100, 1)
    if not l1_ok and not prod_rows:
        snap.health_status = "unknown"
        snap.health_score = None
    return snap


async def refresh_all_snapshots(db: AsyncSession) -> int:
    catalog_ids = (await db.execute(select(ModelCatalog.id))).scalars().all()
    for cid in catalog_ids:
        await upsert_health_snapshot(db, cid)
    return len(catalog_ids)
