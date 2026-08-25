"""v9.6 Model Setup Center API (spec §32–§34, §70–§71, §100)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory, get_db
from app.model_autopilot.autoconfig_job import (
    REQUIRED_ROLES,
    ROLE_DISPLAY,
    rollback_auto_config,
)
from app.model_autopilot.health import upsert_health_snapshot
from app.model_autopilot.probe import probe_model_ping
from app.model_autopilot import service as model_service
from app.models import ModelAutoConfigRun

router = APIRouter(prefix="/api/model-setup", tags=["model-setup"])


async def _enqueue_run(run_id: uuid.UUID, job_name: str) -> None:
    """Best-effort ARQ kick; the run row survives for the UI either way."""
    from arq import create_pool
    from arq.connections import RedisSettings
    import redis.asyncio.connection as _rc
    import os

    _rc.AbstractConnection.lib_name = None
    _rc.AbstractConnection.lib_version = None
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    parts = redis_url.replace("redis://", "").split(":")
    pool = await create_pool(
        RedisSettings(host=parts[0], port=int(parts[1].split("/")[0]) if len(parts) > 1 else 6379)
    )
    try:
        await pool.enqueue_job(job_name, str(run_id), _job_id=f"{job_name}:{run_id}")
    finally:
        await pool.close()


def _serialize_run(run: ModelAutoConfigRun) -> dict:
    return {
        "id": str(run.id),
        "action": run.action,
        "scan_mode": run.scan_mode,
        "status": run.status,
        "phase": run.phase,
        "progress": run.progress,
        "current_model": run.current_model,
        "finished": run.finished,
        "total": run.total,
        "detected_models": run.detected_models,
        "healthy_models": run.healthy_models,
        "eligible_models": run.eligible_models,
        "recommendation_json": run.recommendation_json,
        "before_snapshot": run.before_snapshot,
        "after_snapshot": run.after_snapshot,
        "error_json": run.error_json,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


@router.post("/detect")
async def detect_now(request: Request, db: AsyncSession = Depends(get_db)):
    """Queued model detection (spec §32/§35). Never mutates current config."""
    idem = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
    if idem:
        existing = (
            await db.execute(
                select(ModelAutoConfigRun).where(ModelAutoConfigRun.idempotency_key == idem)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _serialize_run(existing)
    run = ModelAutoConfigRun(
        id=uuid.uuid4(),
        action="detect",
        scan_mode="quick",
        status="queued",
        phase="queued",
        idempotency_key=idem,
    )
    db.add(run)
    await db.commit()
    try:
        await _enqueue_run(run.id, "run_model_detection_job")
    except Exception:  # noqa: BLE001 - UI can still poll; cron/retry not needed
        pass
    return _serialize_run(run)


@router.post("/auto-configure")
async def auto_configure(request: Request, db: AsyncSession = Depends(get_db)):
    """One-click smart configure (spec §33/§96)."""
    idem = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
    run = ModelAutoConfigRun(
        id=uuid.uuid4(),
        action="detect_and_configure",
        scan_mode="quick",
        status="queued",
        phase="queued",
        idempotency_key=idem,
    )
    db.add(run)
    await db.commit()
    try:
        await _enqueue_run(run.id, "run_model_autoconfigure_job")
    except Exception:  # noqa: BLE001
        pass
    return _serialize_run(run)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, db: AsyncSession = Depends(get_db)):
    run = (
        await db.execute(
            select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "run not found")
    if run.status in ("queued", "running"):
        run.status = "cancelled"
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
    return _serialize_run(run)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    run = (
        await db.execute(
            select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "run not found")
    return _serialize_run(run)


@router.get("/current")
async def current_setup(db: AsyncSession = Depends(get_db)):
    """Last run + latest recommendation (spec §25 header info)."""
    last = (
        await db.execute(
            select(ModelAutoConfigRun)
            .order_by(ModelAutoConfigRun.created_at.desc())
            .limit(5)
        )
    ).scalars().all()
    latest_detect = next((r for r in last if r.status == "succeeded"), None)
    return {
        "last_run": _serialize_run(last[0]) if last else None,
        "recent_runs": [_serialize_run(r) for r in last],
        "recommendation": latest_detect.recommendation_json if latest_detect else None,
        "roles": [{"role": r, "label": ROLE_DISPLAY.get(r, r)} for r in REQUIRED_ROLES],
    }


@router.get("/recommendation")
async def recommendation(db: AsyncSession = Depends(get_db)):
    latest = (
        await db.execute(
            select(ModelAutoConfigRun)
            .where(ModelAutoConfigRun.status == "succeeded")
            .order_by(ModelAutoConfigRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "recommendation": latest.recommendation_json if latest else None,
        "run_id": str(latest.id) if latest else None,
    }


@router.post("/rollback/{run_id}")
async def rollback(run_id: str, db: AsyncSession = Depends(get_db)):
    """Spec §29: undo the last auto-configuration."""
    run = (
        await db.execute(
            select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "run not found")
    result = await rollback_auto_config(db, run)
    await db.commit()
    return result


@router.get("/performance")
async def performance(window: str = "24h", db: AsyncSession = Depends(get_db)):
    """v9.7 §17/§70: REAL aggregator (per-model P50/P95/TPS) — never first-row."""
    from app.model_autopilot.performance import aggregate

    return await aggregate(db, window)


@router.get("/models/{catalog_id}")
async def model_detail(catalog_id: str, db: AsyncSession = Depends(get_db)):
    return await model_service.model_row(db, catalog_id) or {}


@router.get("/models/{catalog_id}/timeseries")
async def model_timeseries(catalog_id: str, metric: str = "ttft", window: str = "24h", db: AsyncSession = Depends(get_db)):
    """Spec §71: coarse per-hour performance series from probe rows."""
    from app.models import ModelHealthProbe

    hours = 24 if window == "24h" else (7 * 24 if window == "7d" else 1)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        (
            await db.execute(
                select(ModelHealthProbe)
                .where(
                    ModelHealthProbe.model_catalog_id == uuid.UUID(catalog_id),
                    ModelHealthProbe.started_at >= cutoff,
                    ModelHealthProbe.probe_type.in_(("performance", "production", "l1_ping")),
                )
                .order_by(ModelHealthProbe.started_at.asc())
            )
        )
        .scalars()
        .all()
    )
    series = []
    for r in rows:
        value = None
        if metric == "ttft" and r.first_token_ms is not None:
            value = r.first_token_ms
        elif metric == "tokens_per_second" and r.tokens_per_second is not None:
            value = r.tokens_per_second
        elif metric == "latency" and r.latency_ms is not None:
            value = r.latency_ms
        series.append(
            {
                "t": r.started_at.isoformat() if r.started_at else None,
                "value": value,
                "status": r.status,
            }
        )
    return {"metric": metric, "window": window, "series": series}

# ── v9.7 Model Evaluation / Certification (spec §13.42, §32) ──


@router.get("/evaluation/suites")
async def eval_suites(db: AsyncSession = Depends(get_db)):
    from app.model_eval.engine import seed_suites
    from app.models import ModelEvalCase, ModelEvalSuite

    seed_suites(db)
    await db.commit()
    suites = (await db.execute(select(ModelEvalSuite).order_by(ModelEvalSuite.suite_key))).scalars().all()
    return {
        "items": [
            {
                "suite_key": s.suite_key,
                "version": s.version,
                "name": s.name,
                "target_role": s.target_role,
                "case_count": s.case_count,
                "pass_threshold": s.pass_threshold,
                "is_active": s.is_active,
                "is_private": s.is_private,
            }
            for s in suites
        ]
    }


@router.post("/evaluation/models/{catalog_id}/qualify")
async def qualify_model(catalog_id: str, db: AsyncSession = Depends(get_db)):
    """Tier 1 qualification run: suites + grading → certification (spec §13.11)."""
    import uuid as _uuid
    from app.model_eval.engine import run_qualification
    from app.models import ModelCatalog, ModelEvalRun

    catalog = (
        await db.execute(
            select(ModelCatalog).where(ModelCatalog.id == _uuid.UUID(catalog_id))
        )
    ).scalar_one_or_none()
    if catalog is None:
        raise HTTPException(404, "model not found")
    try:
        run = ModelEvalRun(id=_uuid.uuid4(), model_catalog_id=catalog.id, mode="qualification")
        db.add(run)
        await db.commit()
        result = await run_qualification(db, run)
        return {"run_id": str(run.id), **result}
    except Exception as e:  # noqa: BLE001 - report for UI
        await db.rollback()
        raise HTTPException(500, f"qualification failed: {e}")


@router.post("/evaluation/models/{catalog_id}/context-certify")
async def context_certify(catalog_id: str, db: AsyncSession = Depends(get_db)):
    """Context ladder: declared/accepted/effective measurement (spec §13.22–§13.27)."""
    import uuid as _uuid
    from app.model_eval.engine import run_context_ladder
    from app.models import ModelCatalog, ModelEvalRun

    catalog = (
        await db.execute(
            select(ModelCatalog).where(ModelCatalog.id == _uuid.UUID(catalog_id))
        )
    ).scalar_one_or_none()
    if catalog is None:
        raise HTTPException(404, "model not found")
    try:
        run = ModelEvalRun(id=_uuid.uuid4(), model_catalog_id=catalog.id, mode="context_ladder")
        db.add(run)
        await db.commit()
        result = await run_context_ladder(db, run, catalog)
        return {"run_id": str(run.id), **result}
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        raise HTTPException(500, f"context certify failed: {e}")


@router.post("/evaluation/runs/{run_id}/cancel")
async def cancel_eval_run(run_id: str, db: AsyncSession = Depends(get_db)):
    from app.models import ModelEvalRun

    run = (
        await db.execute(
            select(ModelEvalRun).where(ModelEvalRun.id == uuid.UUID(run_id))
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "run not found")
    run.cancel_requested = True  # runner checks between cases (real stop, spec §13.51)
    await db.commit()
    return {"cancelled": True}


@router.get("/evaluation/runs/{run_id}")
async def get_eval_run(run_id: str, db: AsyncSession = Depends(get_db)):
    from app.models import ModelEvalCaseResult, ModelEvalRun

    run = (
        await db.execute(
            select(ModelEvalRun).where(ModelEvalRun.id == uuid.UUID(run_id))
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "run not found")
    results = (
        (await db.execute(select(ModelEvalCaseResult).where(ModelEvalCaseResult.run_id == run.id).limit(500)))
        .scalars()
        .all()
    )
    return {
        "id": str(run.id),
        "mode": run.mode,
        "status": run.status,
        "overall_score": run.overall_score,
        "confidence": run.confidence,
        "result_summary": run.result_summary,
        "cases": [
            {
                "case_id": str(r.case_id),
                "score": r.score,
                "passed": r.passed,
                "error_code": r.error_code,
                "latency_ms": r.latency_ms,
            }
            for r in results
        ],
    }


@router.get("/evaluation/models/{catalog_id}/certification")
async def get_certification(catalog_id: str, db: AsyncSession = Depends(get_db)):
    from app.models import ModelCatalog

    catalog = (
        await db.execute(
            select(ModelCatalog).where(ModelCatalog.id == uuid.UUID(catalog_id))
        )
    ).scalar_one_or_none()
    if catalog is None:
        raise HTTPException(404, "model not found")
    return {
        "model_kind": catalog.model_kind,
        "text_generation_eligible": catalog.text_generation_eligible,
        "classification_source": catalog.classification_source,
        "evaluation_status": catalog.evaluation_status,
        "certification_level": catalog.certification_level,
        "certification_confidence": catalog.certification_confidence,
        "benchmark_revision": catalog.benchmark_revision,
        "last_certified_at": catalog.last_certified_at.isoformat() if catalog.last_certified_at else None,
        "exclusion_reason": catalog.evaluation_exclusion_reason,
    }


@router.get("/evaluation/models/{catalog_id}/context-profile")
async def get_context_profile(catalog_id: str, db: AsyncSession = Depends(get_db)):
    from app.models import ModelContextProfile

    profile = (
        await db.execute(
            select(ModelContextProfile).where(
                ModelContextProfile.model_catalog_id == uuid.UUID(catalog_id)
            )
        )
    ).scalar_one_or_none()
    if profile is None:
        return {"status": "not_verified"}
    return {
        "declared_context_window": profile.declared_context_window,
        "accepted_context_window": profile.accepted_context_window,
        "effective_context_window": profile.effective_context_window,
        "position_robustness_score": profile.position_robustness_score,
        "multi_hop_score": profile.multi_hop_score,
        "instruction_retention_score": profile.instruction_retention_score,
        "belief_boundary_score": profile.belief_boundary_score,
        "confidence": profile.confidence,
        "last_verified_at": profile.last_verified_at.isoformat() if profile.last_verified_at else None,
    }
