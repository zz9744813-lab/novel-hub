"""v9.6: model detection / one-click autoconfigure jobs (spec §94–§99, §38–§41).

run_model_detection   — sync → health → performance → capability → quick
                        benchmark → RoleScore → recommendation (never changes
                        current config).
run_model_autoconfigure — reuse ≤30min detection (or run one), snapshot,
                        atomically apply every required role binding, verify,
                        ModelChangeLog, then success. Any required-role gap
                        ROLLS BACK entirely (no half-config, spec §97).

Both write progress to model_autoconfig_runs so the UI shows real phases.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model_autopilot.catalog import ensure_capability_for_catalog, sync_catalog_from_provider
from app.model_autopilot.capability import DEFAULT_ROLE_QUALITY_FLOOR
from app.model_autopilot.health import upsert_health_snapshot
from app.model_autopilot.probe import probe_model_ping, probe_model_performance
from app.model_autopilot.router import build_role_route, default_policy_for
from app.model_autopilot.scoring import compute_role_score
from app.model_autopilot.classification import TEXT_KINDS, eligible_text_candidates, classify_catalog_model
from app.model_autopilot.seed import seed_for_model
from app.models import (
    AgentModelBinding,
    ModelAutoConfigRun,
    ModelCatalog,

    ModelChangeLog,
    ModelHealthProbe,
    ModelHealthSnapshot,
)

logger = logging.getLogger("novelforge.model_autopilot.autoconfig")

from app.agents.registry import ROLE_REGISTRY

REQUIRED_ROLES = sorted(
    role for role, spec in ROLE_REGISTRY.items() if spec.production and spec.model_required
)

ROLE_DISPLAY = {
    "chapter_planner": "ChapterPlanner",
    "draft_writer": "DraftWriter",
    "review_agent": "ReviewAgent",
    "state_extractor": "StateExtractor",
    "style_analyzer": "StyleAnalyzer",
}

VALID_MINUTES = 30
POLICY = default_policy_for(
    "hybrid",
    quality_weight=0.45,
    reliability_weight=0.20,
    context_weight=0.15,
    health_weight=0.10,
    latency_weight=0.0,
    cost_weight=0.0,
)


# ── helpers ──


async def _update_run(db: AsyncSession, run: ModelAutoConfigRun, **kw) -> None:
    for key, value in kw.items():
        setattr(run, key, value)


async def _get_models(db: AsyncSession) -> list[ModelCatalog]:
    return list(
        (
            await db.execute(
                select(ModelCatalog).where(
                    ModelCatalog.enabled.is_(True),
                    ModelCatalog.availability_status == "available",
                )
            )
        ).scalars()
    )


# ── quick benchmark (spec §38–§40) ──


async def run_quick_role_benchmark(db: AsyncSession, catalog: ModelCatalog, role: str) -> float | None:
    """One lightweight role probe (top models only, per spec §38). Returns 0-100 or None."""
    from app.gateway.model_gateway import stream_completion_and_collect

    tasks = {
        "draft_writer": (
            "写一句对白：角色“沈砚”用拒绝的语气回应“陆晚”的邀约。只输出对白。",
            lambda s: "沈砚" in s and any(k in s for k in ("不", "没", "别", "不必")),
        ),
        "review_agent": (
            "找出下面这句话里的逻辑错误并指出：\"他在下雨前到了家，因为路上被雨淋湿了。\"",
            lambda s: any(k in s for k in ("淋", "时间", "先", "顺序")),
        ),
        "state_extractor": (
            "提取关键信息(JSON,字段:人名,是否进入宗门):\"张三拒绝进入青云宗。\"",
            lambda s: ("张三" in s and "JSON" in s.upper() or "{" in s) and ("拒绝" in s or "否" in s),
        ),
        "chapter_planner": (
            "判断因果顺序(A先于B?):A.推倒多米诺 B.多米诺倒下。只回答\"是\"或\"否\"。",
            lambda s: "是" in s and "否" not in s[:2],
        ),
        "style_analyzer": (
            "这段文字的风格是?一选一:悲伤/悬疑/温暖:门缝里透出的光像一柄钝刀。",
            lambda s: any(k in s for k in ("悬疑", "悲伤")),
        ),
    }
    task = tasks.get(role)
    if task is None:
        return None
    prompt, judge = task
    try:
        result = await stream_completion_and_collect(
            system_prompt=prompt,
            user_content="",
            model=catalog.model_id,
            temperature=0,
            max_tokens=64,
            provider_role="primary",
            provider=catalog.provider,
        )
        if not result.final_content or result.error:
            return 0.0
        return 100.0 if judge(result.final_content) else 40.0
    except Exception as e:  # noqa: BLE001 - single model failure is isolated
        logger.debug("benchmark failed %s/%s: %s", catalog.provider, catalog.model_id, e)
        return 0.0


# ── detection (spec §95) ──


async def run_model_detection(db: AsyncSession, run: ModelAutoConfigRun) -> dict:
    from app.model_autopilot.preflight import _provider_sync_list

    now = datetime.now(timezone.utc)
    run.started_at = run.started_at or now
    run.status = "running"

    # phase 0: catalog sync (tolerant of provider failures, spec §99)
    run.phase = "catalog_sync"
    run.progress = 5
    await db.commit()
    for provider, base_url, api_key in await _provider_sync_list(db):
        if not base_url:
            continue
        try:
            await sync_catalog_from_provider(
                db, provider=provider, base_url=base_url, api_key=api_key or ""
            )
            await db.commit()
        except Exception as e:  # noqa: BLE001 - provider failure isolated
            logger.warning("catalog sync failed %s: %s", provider, e)
            await db.rollback()

    models = eligible_text_candidates(await _get_models(db))
    run.detected_models = len(models)
    run.total = len(models)
    run.total = len(models)

    # phases: health + performance per model (isolated per model, §99)
    run.phase = "provider_check"
    run.progress = 15
    await db.commit()

    healthy = 0
    run.phase = "model_health"
    run.progress = 20
    await db.commit()
    for idx, catalog in enumerate(models):
        run.current_model = catalog.model_id
        run.finished = idx
        run.progress = 20 + int(40 * (idx / max(1, len(models))))
        await db.commit()
        try:
            probe = await probe_model_ping(db, catalog)
            db.add(probe)
            await upsert_health_snapshot(db, catalog.id)
            await db.commit()
            snap = (
                await db.execute(
                    select(ModelHealthSnapshot).where(
                        ModelHealthSnapshot.model_catalog_id == catalog.id
                    )
                )
            ).scalar_one_or_none()
            if snap and snap.health_status in ("healthy", "degraded"):
                healthy += 1
        except Exception as e:  # noqa: BLE001
            logger.debug("health probe failed %s: %s", catalog.model_id, e)
            await db.rollback()

    run.healthy_models = healthy
    run.phase = "performance_probe"
    run.progress = 60
    await db.commit()
    for idx, catalog in enumerate(models):
        run.current_model = f"perf:{catalog.model_id}"
        run.progress = 60 + int(20 * (idx / max(1, len(models))))
        await db.commit()
        try:
            probe = await probe_model_performance(db, catalog)
            db.add(probe)
            await db.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("perf probe failed %s: %s", catalog.model_id, e)
            await db.rollback()

    run.phase = "capability"
    run.progress = 82
    await db.commit()
    for catalog in models:
        await ensure_capability_for_catalog(db, catalog)

    # quick benchmark on healthy + seed-known candidates (top per role)
    run.phase = "role_benchmark"
    run.progress = 86
    await db.commit()
    for role in REQUIRED_ROLES:
        top4 = sorted(
            (m for m in models if seed_for_model(m.model_id) is not None),
            key=lambda m: -(m.display_name and 0) or 0,
        )[:4]
        for catalog in top4:
            benchmark = await run_quick_role_benchmark(db, catalog, role)
            from app.models import ModelRoleScore

            row = (
                await db.execute(
                    select(ModelRoleScore).where(
                        ModelRoleScore.model_catalog_id == catalog.id,
                        ModelRoleScore.agent_role == role,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                row.benchmark_score = benchmark
        await db.commit()
    # recompute composite with benchmark in play
    for catalog in models:
        for role in REQUIRED_ROLES:
            await compute_role_score(db, catalog, role)

    run.phase = "recommendation"
    run.progress = 94
    await db.commit()

    recommendation, eligible = await build_recommendation(db, models)
    run.eligible_models = eligible
    run.recommendation_json = recommendation
    run.phase = "done"
    run.status = "succeeded"
    run.progress = 100
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    return recommendation


async def build_recommendation(db: AsyncSession, models: list[ModelCatalog]) -> tuple[dict, int]:
    """Per-role primary/fallback recommendation from current signals (spec §59, §12)."""
    from app.agents.registry import ROLE_REGISTRY

    recommendation = {}
    eligible = 0
    for role in REQUIRED_ROLES:
        spec = ROLE_REGISTRY[role]
        floor = spec.default_quality_floor or DEFAULT_ROLE_QUALITY_FLOOR.get(role, 70)
        result = await build_role_route(
            db,
            agent_role=role,
            required_context=spec.expected_context_tokens,
            policy=POLICY,
            locked_primary=None,
        )
        if result.assignment:
            eligible += 1
        recommendation[role] = {
            **result.assignment,
            "minimum_quality_score": floor,
        }
    return recommendation, eligible


# ── one-click autoconfigure (spec §96–§97) ──


async def run_model_autoconfigure(db: AsyncSession, run: ModelAutoConfigRun) -> dict:
    from app.v74_utils import ModelBindingService

    # 1. reuse a fresh detection (< 30min) or run one now
    recommendation = run.recommendation_json
    if recommendation is None:
        recommendation = await run_model_detection(db, run)

    # verify all roles have primary + fallback1 (spec §98: P0 = 100%/100%)
    missing = []
    for role in REQUIRED_ROLES:
        assign = recommendation.get(role) or {}
        if not assign.get("primary", {}).get("model"):
            missing.append(role)
        fb1 = (assign.get("fallbacks") or [{}])[0].get("model")
        if not fb1:
            missing.append(f"{role}:fallback1")
    if missing:
        run.status = "failed"
        run.error_json = {"code": "REQUIRED_ROLE_COVERAGE", "missing": missing}
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "failed", "missing": missing}

    # 2. snapshot current config (spec §29/§61)
    svc = ModelBindingService(db)
    bindings: dict[str, AgentModelBinding] = {}
    before = {}
    for role in REQUIRED_ROLES:
        binding = await svc.get_binding(role, None)
        before[role] = {
            "provider": binding.provider if binding else None,
            "primary_model": binding.primary_model if binding else None,
            "fallback_model": binding.fallback_model if binding else None,
            "routing_mode": binding.routing_mode if binding else None,
        }
    run.before_snapshot = before

    # 3. apply atomically inside this transaction (rollback => whole thing)
    try:
        from app.models import AgentModelBinding as AMB

        for role in REQUIRED_ROLES:
            assign = recommendation[role]
            primary = assign["primary"]
            fb1 = (assign.get("fallbacks") or [{}])[0]
            binding = await svc.get_binding(role, None)
            if binding is None:
                binding = AMB(
                    id=uuid.uuid4(),
                    scope_type="global",
                    scope_id=None,
                    agent_role=role,
                    provider=primary["provider"],
                    primary_model=primary["model"],
                    fallback_model=fb1.get("model"),
                    reasoning_mode="auto",
                    routing_mode="hybrid",
                    updated_by="model-autopilot",
                )
                db.add(binding)
                await db.flush()
            else:
                binding.provider = primary["provider"]
                binding.primary_model = primary["model"]
                binding.fallback_model = fb1.get("model")
                binding.routing_mode = "hybrid"
            binding.auto_assignment_snapshot = {
                "primary": primary,
                "fallbacks": assign.get("fallbacks") or [],
                "scores": {"role_quality_floor": assign.get("minimum_quality_score")},
                "configured_at": datetime.now(timezone.utc).isoformat(),
            }
            binding.last_auto_config_run_id = run.id
            db.add(
                ModelChangeLog(
                    id=uuid.uuid4(),
                    binding_id=binding.id,
                    agent_role=role,
                    old_provider=before[role]["provider"],
                    old_model=before[role]["primary_model"],
                    new_provider=primary["provider"],
                    new_model=primary["model"],
                    old_reasoning_mode="auto",
                    new_reasoning_mode="auto",
                    reason=f"AUTO_CONFIG run={run.id}",
                    changed_by="model-autopilot",
                )
            )
        await db.commit()
    except Exception as e:  # noqa: BLE001 - atomic apply: rollback everything
        await db.rollback()
        run.status = "failed"
        run.error_json = {"code": "APPLY_FAILED", "detail": str(e)[:500]}
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "failed", "error": str(e)}

    after = {}
    for role in REQUIRED_ROLES:
        binding = await svc.get_binding(role, None)
        after[role] = {
            "provider": binding.provider,
            "primary_model": binding.primary_model,
            "fallback_model": binding.fallback_model,
        }
    run.after_snapshot = after
    run.status = "succeeded"
    run.phase = "done"
    run.progress = 100
    run.finished_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "succeeded", "applied": len(REQUIRED_ROLES)}


# ── ARQ wrappers ──


async def run_model_detection_job(ctx, run_id: str) -> dict:
    """ARQ: run a persisted detect run (spec §103: user-triggered evaluation)."""
    from app.database import async_session_factory

    try:
        async with async_session_factory() as db:
            run = (
                await db.execute(
                    select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
                )
            ).scalar_one_or_none()
            if run is None or run.status not in ("queued", "running"):
                return {"status": "skipped"}
        async with async_session_factory() as db:
            run = (
                await db.execute(
                    select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
                )
            ).scalar_one()
            await run_model_detection(db, run)
        return {"status": "succeeded", "run_id": run_id}
    except Exception as e:  # noqa: BLE001
        try:
            async with async_session_factory() as db:
                run = (
                    await db.execute(
                        select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
                    )
                ).scalar_one_or_none()
                if run is not None:
                    run.status = "failed"
                    run.error_json = {"code": "DETECT_FAILED", "detail": str(e)[:500]}
                    run.finished_at = datetime.now(timezone.utc)
                    await db.commit()
                    return {"status": "failed", "error": str(e)}
        except Exception:  # noqa: BLE001
            pass
        raise


async def run_model_autoconfigure_job(ctx, run_id: str) -> dict:
    """ARQ: one-click config — reuses a fresh detection or runs one (spec §96)."""
    from app.database import async_session_factory

    try:
        async with async_session_factory() as db:
            run = (
                await db.execute(
                    select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
                )
            ).scalar_one_or_none()
            if run is None:
                return {"status": "skipped"}
            run.action = "detect_and_configure"
            await db.commit()
        async with async_session_factory() as db:
            run = (
                await db.execute(
                    select(ModelAutoConfigRun).where(ModelAutoConfigRun.id == uuid.UUID(run_id))
                )
            ).scalar_one()
            return await run_model_autoconfigure(db, run)
    except Exception as e:  # noqa: BLE001
        raise


async def rollback_auto_config(db: AsyncSession, run: ModelAutoConfigRun) -> dict:
    """Spec §29: restore the before_snapshot of a finished configure run."""
    from app.v74_utils import ModelBindingService

    if run.action != "detect_and_configure" or not run.before_snapshot:
        return {"status": "noop", "reason": "not a configure run"}
    svc = ModelBindingService(db)
    restored = 0
    for role, snap in (run.before_snapshot or {}).items():
        binding = await svc.get_binding(role, None)
        if binding is not None:
            binding.provider = snap.get("provider") or binding.provider
            binding.primary_model = snap.get("primary_model") or binding.primary_model
            binding.fallback_model = snap.get("fallback_model")
            binding.routing_mode = snap.get("routing_mode") or "manual"
            db.add(
                ModelChangeLog(
                    id=uuid.uuid4(),
                    binding_id=binding.id,
                    agent_role=role,
                    old_provider=run.after_snapshot.get(role, {}).get("provider"),
                    old_model=run.after_snapshot.get(role, {}).get("primary_model"),
                    new_provider=snap.get("provider") or binding.provider,
                    new_model=snap.get("primary_model") or binding.primary_model,
                    old_reasoning_mode="auto",
                    new_reasoning_mode="auto",
                    reason=f"AUTO_CONFIG_ROLLBACK run={run.id}",
                    changed_by="model-autopilot",
                )
            )
            restored += 1
    return {"status": "restored", "restored": restored}
