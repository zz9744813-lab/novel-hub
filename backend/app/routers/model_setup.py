"""v9.6 Model Setup Center API (spec §32–§34, §70–§71, §100)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.model_autopilot.autoconfig_job import (
    REQUIRED_ROLES,
    ROLE_DISPLAY,
    rollback_auto_config,
)
from app.model_autopilot import service as model_service
from app.models import ModelAutoConfigRun

router = APIRouter(prefix="/api/model-setup", tags=["model-setup"])


async def _enqueue_run(run_id: uuid.UUID, job_name: str) -> None:
    """Enqueue a persisted run; callers turn failures into a terminal run."""
    from arq import create_pool
    from arq.connections import RedisSettings
    import redis.asyncio.connection as _rc
    import os

    _rc.AbstractConnection.lib_name = None
    _rc.AbstractConnection.lib_version = None
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    pool = await create_pool(RedisSettings.from_dsn(redis_url))
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


def _serialize_eval_run(run) -> dict:
    return {
        "id": str(run.id),
        "mode": run.mode,
        "status": run.status,
        "overall_score": run.overall_score,
        "confidence": run.confidence,
        "result_summary": run.result_summary,
        "reuse_reason": run.reuse_reason,
        "triggered_by": run.triggered_by,
        "force_requested": run.force_requested,
        "gateway_calls": run.gateway_calls,
        "ability_evaluation_key": run.ability_evaluation_key,
        "ability_source_run_id": str(run.ability_source_run_id) if run.ability_source_run_id else None,
        "context_evaluation_key": run.context_evaluation_key,
        "context_source_run_id": str(run.context_source_run_id) if run.context_source_run_id else None,
        "cancel_requested": run.cancel_requested,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def _parse_uuid(value: str, *, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"invalid {field}") from exc


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
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.phase = "done"
        run.finished_at = datetime.now(timezone.utc)
        run.error_json = {"code": "QUEUE_UNAVAILABLE", "detail": type(exc).__name__}
        await db.commit()
        raise HTTPException(
            503,
            detail={"run_id": str(run.id), "error": "model setup queue unavailable"},
        ) from exc
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
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.phase = "done"
        run.finished_at = datetime.now(timezone.utc)
        run.error_json = {"code": "QUEUE_UNAVAILABLE", "detail": type(exc).__name__}
        await db.commit()
        raise HTTPException(
            503,
            detail={"run_id": str(run.id), "error": "model setup queue unavailable"},
        ) from exc
    return _serialize_run(run)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, db: AsyncSession = Depends(get_db)):
    parsed_id = _parse_uuid(run_id, field="run id")
    run = (
        await db.execute(
            select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == parsed_id)
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
    parsed_id = _parse_uuid(run_id, field="run id")
    run = (
        await db.execute(
            select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == parsed_id)
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
    """READ-ONLY list of evaluation suites (v9.8 P0-10 fix).

    This handler MUST NOT seed (no write side-effect). Suites are ensured by
    explicit write/eval paths (detect, preflight, qualify, context-certify),
    never by a GET.
    """
    from app.models import ModelEvalSuite

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
                "pass_threshold_pct": int(s.pass_threshold * 100 if s.pass_threshold <= 1 else s.pass_threshold),
                "is_active": s.is_active,
                "is_private": s.is_private,
                "mode": s.mode,
            }
            for s in suites
        ]
    }


async def _submit_evaluation(
    *,
    catalog_id: str,
    mode: str,
    force: bool,
    db: AsyncSession,
) -> dict:
    from app.model_eval.engine import (
        ensure_v98_suites,
        get_catalog_evidence_state,
        run_context_ladder,
        run_qualification,
    )
    from app.models import ModelCatalog, ModelEvalRun

    parsed_id = _parse_uuid(catalog_id, field="catalog id")
    catalog = (
        await db.execute(select(ModelCatalog).where(ModelCatalog.id == parsed_id))
    ).scalar_one_or_none()
    if catalog is None:
        raise HTTPException(404, "model not found")
    if not catalog.text_generation_eligible:
        raise HTTPException(409, "non-text model is excluded from text evaluation")

    await ensure_v98_suites(db)
    await db.commit()
    run = ModelEvalRun(
        id=uuid.uuid4(),
        model_catalog_id=catalog.id,
        mode=mode,
        status="queued",
        force_requested=force,
    )
    db.add(run)
    await db.commit()

    # A current cache entry completes synchronously and is guaranteed to make
    # zero gateway calls.  First runs and forced runs are always background jobs.
    state = await get_catalog_evidence_state(db, catalog)
    state_key = "ability" if mode == "qualification" else "context"
    if not force and state.get(state_key, {}).get("state") == "valid":
        async def _unexpected_gateway(**kwargs):
            raise RuntimeError("cache precheck diverged")

        result = (
            await run_qualification(db, run, force=False, gateway=_unexpected_gateway)
            if mode == "qualification"
            else await run_context_ladder(db, run, catalog, force=False, gateway=_unexpected_gateway)
        )
        return {"run_id": str(run.id), "queued": False, **result}

    job_name = (
        "run_model_qualification_job"
        if mode == "qualification"
        else "run_model_context_certification_job"
    )
    try:
        await _enqueue_run(run.id, job_name)
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.result_summary = {
            "execution_complete": False,
            "error": f"enqueue_failed:{type(exc).__name__}",
        }
        await db.commit()
        raise HTTPException(
            503,
            detail={
                "run_id": str(run.id),
                "status": "failed",
                "error": "evaluation queue unavailable",
            },
        ) from exc
    return {"run_id": str(run.id), "queued": True, **_serialize_eval_run(run)}


@router.post("/evaluation/models/{catalog_id}/qualify")
async def qualify_model(catalog_id: str, db: AsyncSession = Depends(get_db), force: bool = False):
    """Reuse immediately or enqueue a versioned ability evaluation."""

    return await _submit_evaluation(
        catalog_id=catalog_id,
        mode="qualification",
        force=force,
        db=db,
    )


@router.post("/evaluation/models/{catalog_id}/context-certify")
async def context_certify(catalog_id: str, db: AsyncSession = Depends(get_db), force: bool = False):
    """Reuse immediately or enqueue the adaptive context ladder."""

    return await _submit_evaluation(
        catalog_id=catalog_id,
        mode="context_ladder",
        force=force,
        db=db,
    )


@router.post("/evaluation/models/{catalog_id}/force-retest")
async def force_retest(catalog_id: str, db: AsyncSession = Depends(get_db)):
    """v9.8: explicit, confirmable force re-qualification (bypasses cache)."""
    return await _submit_evaluation(
        catalog_id=catalog_id,
        mode="qualification",
        force=True,
        db=db,
    )


@router.post("/evaluation/runs/{run_id}/cancel")
async def cancel_eval_run(run_id: str, db: AsyncSession = Depends(get_db)):
    from app.models import ModelEvalRun

    parsed_id = _parse_uuid(run_id, field="run id")
    run = (
        await db.execute(
            select(ModelEvalRun).where(ModelEvalRun.id == parsed_id)
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "run not found")
    run.cancel_requested = True  # runner checks between cases/rungs
    if run.status == "queued":
        run.status = "cancelled"
        run.finished_at = datetime.now(timezone.utc)
        run.result_summary = {"execution_complete": False, "error": "cancelled_before_start"}
    await db.commit()
    return {"cancelled": True}


@router.get("/evaluation/runs/{run_id}")
async def get_eval_run(run_id: str, db: AsyncSession = Depends(get_db)):
    from app.models import ModelEvalCaseResult, ModelEvalRun

    parsed_id = _parse_uuid(run_id, field="run id")
    run = (
        await db.execute(
            select(ModelEvalRun).where(ModelEvalRun.id == parsed_id)
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
        **_serialize_eval_run(run),
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


@router.get("/evaluation/evidence")
async def evaluation_evidence(db: AsyncSession = Depends(get_db)):
    """Read-only DTO for evidence, role gates, context, and independent health."""
    from app.model_eval.engine import (
        _ability_suite_hash,
        _context_suite_hash,
        get_catalog_evidence_state,
    )
    from app.models import ModelCatalog, ModelHealthSnapshot

    suite_hashes = (await _ability_suite_hash(db), await _context_suite_hash(db))
    catalogs = (
        await db.execute(select(ModelCatalog).order_by(ModelCatalog.provider, ModelCatalog.model_id))
    ).scalars().all()
    items = []
    for catalog in catalogs:
        snapshot = (
            await db.execute(
                select(ModelHealthSnapshot).where(
                    ModelHealthSnapshot.model_catalog_id == catalog.id
                )
            )
        ).scalar_one_or_none()
        if catalog.text_generation_eligible:
            evidence = await get_catalog_evidence_state(
                db,
                catalog,
                suite_hashes=suite_hashes,
            )
        else:
            evidence = {
                "ability": {"state": "excluded", "reason": "non_text_model", "changed_fields": []},
                "context": {"state": "excluded", "reason": "non_text_model", "changed_fields": []},
                "role_evidence": {},
                "context_profile": {},
                "ability_evaluation_key": None,
                "context_evaluation_key": None,
            }
        items.append(
            {
                "id": str(catalog.id),
                "provider": catalog.provider,
                "model_id": catalog.model_id,
                "display_name": catalog.display_name,
                "model_kind": catalog.model_kind,
                "enabled": catalog.enabled,
                "availability_status": catalog.availability_status,
                "text_generation_eligible": catalog.text_generation_eligible,
                "exclusion_reason": catalog.evaluation_exclusion_reason,
                "ability": evidence.get("ability"),
                "context": evidence.get("context"),
                "ability_evaluation_key": evidence.get("ability_evaluation_key"),
                "context_evaluation_key": evidence.get("context_evaluation_key"),
                "ability_evaluator_revision": catalog.ability_evaluator_revision,
                "context_evaluator_revision": catalog.context_evaluator_revision,
                "ability_source_run_id": (
                    str(catalog.ability_source_run_id) if catalog.ability_source_run_id else None
                ),
                "context_source_run_id": (
                    str(catalog.context_source_run_id) if catalog.context_source_run_id else None
                ),
                "ability_completed_at": (
                    catalog.ability_completed_at.isoformat() if catalog.ability_completed_at else None
                ),
                "context_completed_at": (
                    catalog.context_completed_at.isoformat() if catalog.context_completed_at else None
                ),
                "role_evidence": evidence.get("role_evidence") or {},
                "context_profile": evidence.get("context_profile") or {},
                "health": {
                    "status": snapshot.health_status if snapshot else "unknown",
                    "last_probe_at": (
                        snapshot.last_probe_at.isoformat() if snapshot and snapshot.last_probe_at else None
                    ),
                    "latency_ms": snapshot.p50_latency_ms if snapshot else None,
                    "health_score": snapshot.health_score if snapshot else None,
                },
            }
        )
    return {"items": items}


@router.get("/evaluation/models/{catalog_id}/certification")
async def get_certification(catalog_id: str, db: AsyncSession = Depends(get_db)):
    from app.models import ModelCatalog

    parsed_id = _parse_uuid(catalog_id, field="catalog id")
    catalog = (
        await db.execute(
            select(ModelCatalog).where(ModelCatalog.id == parsed_id)
        )
    ).scalar_one_or_none()
    if catalog is None:
        raise HTTPException(404, "model not found")
    from app.model_eval.engine import get_catalog_evidence_state

    state = await get_catalog_evidence_state(db, catalog) if catalog.text_generation_eligible else None
    return {
        "model_kind": catalog.model_kind,
        "text_generation_eligible": catalog.text_generation_eligible,
        "classification_source": catalog.classification_source,
        "evaluation_status": catalog.evaluation_status,
        "certification_level": catalog.certification_level,
        "certification_confidence": catalog.certification_confidence,
        "benchmark_revision": catalog.benchmark_revision,
        # v9.8: ability evidence fingerprint (reuse / invalidation)
        "ability_evaluation_key": catalog.ability_evaluation_key,
        "ability_identity_hash": catalog.ability_identity_hash,
        "ability_suite_hash": catalog.ability_suite_hash,
        "ability_evaluator_revision": catalog.ability_evaluator_revision,
        "ability_reuse_reason": catalog.ability_reuse_reason,
        "context_evaluation_key": catalog.context_evaluation_key,
        "context_evaluator_revision": catalog.context_evaluator_revision,
        "ability_state": state.get("ability") if state else {"state": "excluded", "reason": "non_text_model"},
        "context_state": state.get("context") if state else {"state": "excluded", "reason": "non_text_model"},
        "role_evidence": state.get("role_evidence") if state else {},
        "ability_source_run_id": str(catalog.ability_source_run_id) if catalog.ability_source_run_id else None,
        "context_source_run_id": str(catalog.context_source_run_id) if catalog.context_source_run_id else None,
        "last_certified_at": catalog.last_certified_at.isoformat() if catalog.last_certified_at else None,
        "exclusion_reason": catalog.evaluation_exclusion_reason,
    }


@router.get("/evaluation/models/{catalog_id}/context-profile")
async def get_context_profile(catalog_id: str, db: AsyncSession = Depends(get_db)):
    from app.models import ModelContextProfile

    parsed_id = _parse_uuid(catalog_id, field="catalog id")
    profile = (
        await db.execute(
            select(ModelContextProfile).where(
                ModelContextProfile.model_catalog_id == parsed_id
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
        "context_evaluation_key": profile.context_evaluation_key,
        "context_identity_hash": profile.context_identity_hash,
        "context_suite_hash": profile.context_suite_hash,
        "context_evaluator_revision": profile.context_evaluator_revision,
        "context_source_run_id": str(profile.context_source_run_id) if profile.context_source_run_id else None,
        "confidence": profile.confidence,
        "last_verified_at": profile.last_verified_at.isoformat() if profile.last_verified_at else None,
    }
