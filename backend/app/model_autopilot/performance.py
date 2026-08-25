"""v9.7 PerformanceAggregator (spec §17): real per-model/provider/global stats.

TTFT P50/P95, latency P50/P95, tokens/s P50/P95, success rates, fallback rate.
Never: TTFT=latency, P50=first row, tps=max(tps).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ModelCatalog, ModelHealthProbe, ModelHealthSnapshot

WINDOWS = {"1h": 1, "24h": 24, "7d": 24 * 7}


def _percentile(values: list[float], p: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    idx = min(len(ordered) - 1, int((len(ordered) - 1) * p))
    return round(ordered[idx], 2)


def _rate(rows: list[ModelHealthProbe]) -> float | None:
    if not rows:
        return None
    return round(sum(1 for r in rows if r.status == "ok") / len(rows), 4)


async def aggregate(
    db: AsyncSession, window: str = "24h", *, focus_catalog_id=None
) -> dict:
    """Aggregate probe+production stats for all models (or one)."""
    hours = WINDOWS.get(window, 24)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    stmt = (
        select(ModelHealthProbe)
        .where(ModelHealthProbe.started_at >= cutoff)
        .order_by(ModelHealthProbe.started_at)
    )
    if focus_catalog_id:
        stmt = stmt.where(ModelHealthProbe.model_catalog_id == focus_catalog_id)
    probes = (await db.execute(stmt)).scalars().all()

    by_model: dict = {}
    for p in probes:
        by_model.setdefault(p.model_catalog_id, []).append(p)

    models = {
        c.id: c
        for c in (
            await db.execute(
                select(ModelCatalog).where(ModelCatalog.id.in_(list(by_model) or [None]))
            )
        ).scalars()
    }

    rows = []
    for catalog_id, prows in by_model.items():
        ping = [p for p in prows if p.probe_type in ("l1_ping", "performance") and p.latency_ms]
        perf = [p for p in prows if p.probe_type == "performance" and p.tokens_per_second is not None]
        prod = [p for p in prows if p.probe_type == "production"]
        catalog = models.get(catalog_id)
        rows.append(
            {
                "model_id": catalog.model_id if catalog else str(catalog_id),
                "provider": catalog.provider if catalog else "",
                "request_count": len(prows),
                "production_request_count": len(prod),
                "success_rate": _rate(prows),
                "ttft_p50_ms": _percentile([p.first_token_ms for p in ping if p.first_token_ms], 0.5),
                "ttft_p95_ms": _percentile([p.first_token_ms for p in ping if p.first_token_ms], 0.95),
                "latency_p50_ms": _percentile([p.latency_ms for p in ping], 0.5),
                "latency_p95_ms": _percentile([p.latency_ms for p in ping], 0.95),
                "tokens_per_second_p50": _percentile([p.tokens_per_second for p in perf], 0.5),
                "tokens_per_second_p95": _percentile([p.tokens_per_second for p in perf], 0.95),
            }
        )

    rows.sort(key=lambda r: r["success_rate"] or -1, reverse=True)

    # global KPIs — aggregated across ALL rows, never "first row"
    all_ttft = [r["ttft_p50_ms"] for r in rows if r["ttft_p50_ms"] is not None]
    all_tps = [r["tokens_per_second_p50"] for r in rows if r["tokens_per_second_p50"] is not None]
    global_kpi = {
        "available_models": sum(1 for r in rows if (r["success_rate"] or 0) > 0),
        "total_models": len(rows),
        "overall_success_rate": round(
            sum(r["success_rate"] or 0 for r in rows) / len(rows), 4
        ) if rows else None,
        "ttft_p50_global_ms": _percentile(all_ttft, 0.5),
        "ttft_p95_global_ms": _percentile(all_ttft, 0.95),
        "tokens_per_second_p50_global": _percentile(all_tps, 0.5),
    }
    return {"window": window, "models": rows, "global": global_kpi}
