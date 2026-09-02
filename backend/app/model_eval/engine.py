"""Database boundary for versioned, reusable model evidence.

The expensive ability and context evaluators run only when their content key is
missing/stale or the caller explicitly forces a retest.  Lightweight health
probes live in ``model_autopilot`` and never call this evaluator.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.model_eval.evidence import (
    ABILITY_EVALUATOR_REVISION,
    CONTEXT_EVALUATOR_REVISION,
    ability_evaluation_key,
    case_definition_hash,
    compute_endpoint_identity_hash,
    compute_upstream_identity_hash,
    context_evaluation_key,
    current_evidence_state,
    decide_ability_reuse_with_parts,
    decide_context_reuse_with_parts,
    grade_response,
    model_identity_hash,
    normalize_endpoint,
    normalize_suite,
    pick_ladder,
    reaggregate_qualification_roles,
    run_context_ladder_core,
    run_qualification_core,
    suite_aggregate_hash,
)
from app.model_eval.suite_definitions import (
    ROUTABLE_ROLES,
    SUITE_VERSION,
    qualification_role_for,
    v98_suite_definitions,
)
from app.models import (
    ModelCapabilityProfile,
    ModelCatalog,
    ModelContextProfile,
    ModelEvalCase,
    ModelEvalCaseResult,
    ModelEvalRun,
    ModelEvalSuite,
    ModelRoleScore,
)


class SuiteDefinitionDriftError(RuntimeError):
    """The database row for an immutable suite version differs from code."""


def _suite_id(suite_key: str, version: str = SUITE_VERSION) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"novelforge:model-eval-suite:{suite_key}:{version}")


def _case_id(suite_key: str, version: str, case_key: str, case_version: str) -> uuid.UUID:
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"novelforge:model-eval-case:{suite_key}:{version}:{case_key}:{case_version}",
    )


def _case_payload(case: ModelEvalCase) -> dict:
    return {
        "id": case.id,
        "case_key": case.case_key,
        "case_version": case.case_version,
        "role": case.role,
        "category": case.category,
        "difficulty": case.difficulty,
        "prompt_template": case.prompt_template,
        "context_template": case.context_template,
        "generator_type": case.generator_type,
        "generator_config": case.generator_config or {},
        "expected_answer": case.expected_answer,
        "expected_schema": case.expected_schema or {},
        "grader_type": case.grader_type,
        "grader_config": case.grader_config or {},
        "max_output_tokens": case.max_output_tokens,
        "temperature": case.temperature,
        "private_case": case.private_case,
        "active": case.active,
        "case_hash": case.case_hash,
    }


def _suite_payload(suite: ModelEvalSuite, cases: list[ModelEvalCase]) -> dict:
    return {
        "id": suite.id,
        "suite_key": suite.suite_key,
        "version": suite.version,
        "name": suite.name,
        "purpose": suite.purpose,
        "target_role": suite.target_role,
        "difficulty": suite.difficulty,
        "mode": suite.mode,
        "case_count": suite.case_count,
        "pass_threshold": suite.pass_threshold,
        "is_active": suite.is_active,
        "is_private": suite.is_private,
        "cases": [_case_payload(case) for case in cases],
    }


def _desired_case_payload(case: dict, *, private_case: bool) -> dict:
    return {
        **case,
        "difficulty": case.get("difficulty") or "medium",
        "context_template": case.get("context_template"),
        "generator_type": case.get("generator_type"),
        "generator_config": case.get("generator_config") or {},
        "expected_schema": case.get("expected_schema") or {},
        "private_case": bool(case.get("private_case", private_case)),
        "active": bool(case.get("active", True)),
    }


async def seed_suites(db: AsyncSession) -> int:
    """Create immutable versioned suites or fail closed on same-version drift."""

    created = 0
    for definition in v98_suite_definitions():
        suite_id = _suite_id(definition["suite_key"], definition["version"])
        suite = (
            await db.execute(select(ModelEvalSuite).where(ModelEvalSuite.id == suite_id))
        ).scalar_one_or_none()
        desired_cases = [
            _desired_case_payload(case, private_case=definition["is_private"])
            for case in definition["cases"]
        ]
        if suite is None:
            suite = ModelEvalSuite(
                id=suite_id,
                suite_key=definition["suite_key"],
                version=definition["version"],
                name=definition["name"],
                purpose=definition["purpose"],
                target_role=definition["target_role"],
                difficulty=definition["difficulty"],
                mode=definition["mode"],
                case_count=len(desired_cases),
                pass_threshold=definition["pass_threshold"],
                is_active=definition["is_active"],
                is_private=definition["is_private"],
            )
            db.add(suite)
            await db.flush()
            for desired in desired_cases:
                db.add(
                    ModelEvalCase(
                        id=_case_id(
                            definition["suite_key"],
                            definition["version"],
                            desired["case_key"],
                            desired["case_version"],
                        ),
                        suite_id=suite_id,
                        case_key=desired["case_key"],
                        case_version=desired["case_version"],
                        role=desired.get("role"),
                        category=desired.get("category"),
                        difficulty=desired.get("difficulty"),
                        prompt_template=desired["prompt_template"],
                        context_template=desired.get("context_template"),
                        generator_type=desired.get("generator_type"),
                        generator_config=desired.get("generator_config") or {},
                        expected_answer=desired.get("expected_answer"),
                        expected_schema=desired.get("expected_schema") or {},
                        grader_type=desired["grader_type"],
                        grader_config=desired.get("grader_config") or {},
                        max_output_tokens=desired.get("max_output_tokens") or 512,
                        temperature=desired.get("temperature") or 0,
                        private_case=desired["private_case"],
                        active=desired["active"],
                        case_hash=case_definition_hash(desired),
                    )
                )
            await db.flush()
            created += 1
            continue

        scalar_fields = {
            "suite_key": definition["suite_key"],
            "version": definition["version"],
            "name": definition["name"],
            "purpose": definition["purpose"],
            "target_role": definition["target_role"],
            "difficulty": definition["difficulty"],
            "mode": definition["mode"],
            "case_count": len(desired_cases),
            "pass_threshold": definition["pass_threshold"],
            "is_active": definition["is_active"],
            "is_private": definition["is_private"],
        }
        drifted = [name for name, value in scalar_fields.items() if getattr(suite, name) != value]
        existing_cases = (
            await db.execute(select(ModelEvalCase).where(ModelEvalCase.suite_id == suite_id))
        ).scalars().all()
        existing_by_key = {(case.case_key, case.case_version): case for case in existing_cases}
        desired_keys = {(case["case_key"], case["case_version"]) for case in desired_cases}
        if set(existing_by_key) != desired_keys:
            drifted.append("case_set")
        for desired in desired_cases:
            existing = existing_by_key.get((desired["case_key"], desired["case_version"]))
            if existing is None:
                continue
            actual_hash = case_definition_hash(_case_payload(existing))
            desired_hash = case_definition_hash(desired)
            if actual_hash != desired_hash or existing.case_hash != desired_hash:
                drifted.append(f"case:{desired['case_key']}")
        if drifted:
            raise SuiteDefinitionDriftError(
                f"immutable suite {definition['suite_key']}:{definition['version']} drifted: "
                + ", ".join(sorted(set(drifted)))
            )
    return created


async def ensure_v98_suites(db: AsyncSession) -> int:
    """Explicit write/evaluation-path seed; GET handlers must never call this."""

    return await seed_suites(db)


async def _load_v98_suites(db: AsyncSession, *, mode: str | None = None) -> list[dict]:
    payloads = []
    definitions = v98_suite_definitions()
    for definition in definitions:
        if mode is not None and definition["mode"] != mode:
            continue
        suite = (
            await db.execute(
                select(ModelEvalSuite).where(
                    ModelEvalSuite.id == _suite_id(definition["suite_key"], definition["version"])
                )
            )
        ).scalar_one_or_none()
        if suite is None or not suite.is_active:
            continue
        cases = (
            await db.execute(
                select(ModelEvalCase).where(
                    ModelEvalCase.suite_id == suite.id,
                    ModelEvalCase.active.is_(True),
                )
            )
        ).scalars().all()
        cases = sorted(cases, key=lambda case: (case.case_key, case.case_version))
        payloads.append(_suite_payload(suite, cases))
    return payloads


async def _ability_suite_hash(db: AsyncSession) -> str | None:
    suites = await _load_v98_suites(db, mode="qualification")
    expected = sum(1 for suite in v98_suite_definitions() if suite["mode"] == "qualification")
    return suite_aggregate_hash(suites) if len(suites) == expected else None


async def _context_suite_hash(db: AsyncSession) -> str | None:
    suites = await _load_v98_suites(db, mode="context_ladder")
    expected = sum(1 for suite in v98_suite_definitions() if suite["mode"] == "context_ladder")
    return suite_aggregate_hash(suites) if len(suites) == expected else None


def _provider_base_url(catalog: ModelCatalog) -> str | None:
    metadata = dict(catalog.metadata_json or {})
    explicit = metadata.get("routing_endpoint") or metadata.get("base_url") or metadata.get("provider_base_url")
    if explicit:
        return str(explicit)
    provider_key = catalog.provider.upper().replace("-", "_")
    if catalog.provider.lower() in {"new-api", "new_api", "newapi"}:
        return os.environ.get("NEW_API_BASE_URL") or os.environ.get("PRIMARY_BASE_URL")
    if catalog.provider.lower() == "openrouter":
        return os.environ.get("OPENROUTER_BASE_URL") or os.environ.get("PRIMARY_BASE_URL")
    return os.environ.get(f"{provider_key}_BASE_URL") or os.environ.get("PRIMARY_BASE_URL")


def derive_endpoint_identity_hash(catalog: ModelCatalog) -> str:
    base_url = _provider_base_url(catalog)
    if base_url:
        metadata = dict(catalog.metadata_json or {})
        return compute_endpoint_identity_hash(
            base_url=base_url,
            routing_endpoint=metadata.get("routing_endpoint"),
        )
    return (
        getattr(catalog, "endpoint_identity_hash", None)
        or (catalog.metadata_json or {}).get("endpoint_identity_hash")
        or compute_endpoint_identity_hash(base_url=None)
    )


def refresh_endpoint_identity(catalog: ModelCatalog, *, base_url: str | None = None) -> str:
    metadata = dict(catalog.metadata_json or {})
    effective_base_url = base_url or _provider_base_url(catalog)
    routing_endpoint = metadata.get("routing_endpoint")
    if effective_base_url or routing_endpoint:
        endpoint_hash = compute_endpoint_identity_hash(
            base_url=effective_base_url,
            routing_endpoint=routing_endpoint,
        )
    else:
        # Catalog sync may have persisted only the safe one-way hash. Do not
        # replace that real identity with the hash of an empty endpoint merely
        # because this process lacks the provider URL environment variable.
        endpoint_hash = (
            getattr(catalog, "endpoint_identity_hash", None)
            or metadata.get("endpoint_identity_hash")
            or compute_endpoint_identity_hash(base_url=None)
        )
    catalog.endpoint_identity_hash = endpoint_hash
    metadata["endpoint_identity_hash"] = endpoint_hash
    if effective_base_url:
        metadata["endpoint_normalized"] = normalize_endpoint(effective_base_url)
    catalog.metadata_json = metadata
    return endpoint_hash


def refresh_upstream_identity(catalog: ModelCatalog) -> str:
    metadata = dict(catalog.metadata_json or {})
    upstream_hash = compute_upstream_identity_hash(
        owned_by=metadata.get("owned_by"),
        created=metadata.get("created"),
        upstream_revision=metadata.get("upstream_revision"),
    )
    catalog.upstream_identity_hash = upstream_hash
    metadata["upstream_identity_hash"] = upstream_hash
    catalog.metadata_json = metadata
    return upstream_hash


def _identity_hash(catalog: ModelCatalog, endpoint_hash: str) -> str:
    metadata = dict(catalog.metadata_json or {})
    return model_identity_hash(
        provider=catalog.provider,
        model_id=catalog.model_id,
        model_kind=catalog.model_kind,
        endpoint_identity_hash=endpoint_hash,
        owned_by=metadata.get("owned_by"),
        created=metadata.get("created"),
        upstream_revision=metadata.get("upstream_revision"),
    )


def _catalog_payload(catalog: ModelCatalog) -> dict:
    metadata = dict(catalog.metadata_json or {})
    return {
        "provider": catalog.provider,
        "model_id": catalog.model_id,
        "model_kind": catalog.model_kind,
        "text_generation_eligible": catalog.text_generation_eligible,
        "owned_by": metadata.get("owned_by"),
        "created": metadata.get("created"),
        "upstream_revision": metadata.get("upstream_revision"),
    }


async def _default_gateway(**kwargs):
    from app.gateway.model_gateway import _request_model, stream_completion_and_collect

    model = kwargs["model"]
    max_tokens = kwargs.get("max_tokens", 512)
    normalized = str(model).casefold()
    if normalized.startswith("glm-") or "/glm-" in normalized:
        # Some OpenAI-compatible relays ignore GLM's thinking toggle.  Give a
        # one-time evidence case enough room to reach final content even then;
        # lightweight recurring health probes keep their separate small cap.
        max_tokens = max(2048, int(max_tokens or 0))
    # Ability evidence is the one place that needs a deterministic no-thinking
    # DeepSeek request.  Keep the New API suffix out of lightweight health
    # probes so a channel that only advertises the base alias remains healthy.
    request_model = _request_model(model, reasoning_mode="disabled")
    result = await stream_completion_and_collect(
        system_prompt=kwargs["system_prompt"],
        user_content=kwargs["user_content"],
        model=request_model,
        temperature=kwargs.get("temperature", 0),
        max_tokens=max_tokens,
        provider_role="primary",
        provider=kwargs.get("provider"),
        reasoning_mode="disabled",
    )
    # A one-time qualification must not be invalidated by one transient relay
    # failure. Retry server/rate/connectivity failures exactly once and expose
    # the real upstream attempt count to the immutable evidence record.
    if result.error in {
        "CONNECT_TIMEOUT",
        "HTTP_429",
        "HTTP_500",
        "HTTP_502",
        "HTTP_503",
        "HTTP_504",
    }:
        result = await stream_completion_and_collect(
            system_prompt=kwargs["system_prompt"],
            user_content=kwargs["user_content"],
            model=request_model,
            temperature=kwargs.get("temperature", 0),
            max_tokens=max_tokens,
            provider_role="primary",
            provider=kwargs.get("provider"),
            reasoning_mode="disabled",
        )
        result.gateway_calls = 2
    return result


def _latest(rows: list[ModelEvalRun]) -> ModelEvalRun | None:
    floor = datetime.min.replace(tzinfo=timezone.utc)

    def key(run: ModelEvalRun):
        value = run.finished_at or getattr(run, "created_at", None) or floor
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value

    return max(rows, key=key) if rows else None


async def _latest_source_run(
    db: AsyncSession,
    *,
    catalog_id: uuid.UUID,
    mode: str,
    current_run_id: uuid.UUID,
    evaluation_key: str,
) -> ModelEvalRun | None:
    rows = (
        await db.execute(
            select(ModelEvalRun).where(
                ModelEvalRun.model_catalog_id == catalog_id,
                ModelEvalRun.mode == mode,
                ModelEvalRun.status == "succeeded",
            )
        )
    ).scalars().all()
    source_field = "ability_source_run_id" if mode == "qualification" else "context_source_run_id"
    key_field = "ability_evaluation_key" if mode == "qualification" else "context_evaluation_key"
    originals = [
        row
        for row in rows
        if row.id != current_run_id
        and getattr(row, key_field, None)
        and getattr(row, source_field, None) is None
        and bool((row.result_summary or {}).get("execution_complete", True))
    ]
    # Evidence is content-addressed. If the endpoint/model identity later
    # returns to a previously evaluated value, reuse that matching source even
    # when a newer source exists for a different identity.
    matching = [row for row in originals if getattr(row, key_field, None) == evaluation_key]
    return _latest(matching) or _latest(originals)


async def _claim_run(
    db: AsyncSession,
    *,
    catalog_id: uuid.UUID,
    mode: str,
    run: ModelEvalRun,
    stale_after: timedelta = timedelta(minutes=30),
) -> uuid.UUID | None:
    """Take a short catalog-row lock, claim, then commit before network I/O."""

    await db.execute(
        select(ModelCatalog).where(ModelCatalog.id == catalog_id).with_for_update()
    )
    now = datetime.now(timezone.utc)
    active = (
        await db.execute(
            select(ModelEvalRun).where(
                ModelEvalRun.model_catalog_id == catalog_id,
                ModelEvalRun.mode == mode,
                ModelEvalRun.status.in_(["running", "in_progress"]),
            )
        )
    ).scalars().all()
    for existing in active:
        if existing.id == run.id:
            continue
        started = existing.started_at
        if started is not None and started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started is None or started < now - stale_after:
            existing.status = "failed"
            existing.finished_at = now
            existing.result_summary = {
                **(existing.result_summary or {}),
                "execution_complete": False,
                "error": "abandoned_active_claim",
            }
            continue
        run.status = "deduplicated"
        run.reuse_reason = "concurrent_run_in_progress"
        run.triggered_by = "dedup"
        run.finished_at = now
        run.result_summary = {
            "execution_complete": False,
            "source_run_id": str(existing.id),
            "reason": "concurrent_run_in_progress",
        }
        await db.commit()
        return existing.id
    run.status = "running"
    run.started_at = now
    db.add(run)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        active = (
            await db.execute(
                select(ModelEvalRun).where(
                    ModelEvalRun.model_catalog_id == catalog_id,
                    ModelEvalRun.mode == mode,
                    ModelEvalRun.status.in_(["running", "in_progress"]),
                )
            )
        ).scalars().all()
        other = next((item for item in active if item.id != run.id), None)
        if other is None:
            raise
        run.status = "deduplicated"
        run.reuse_reason = "concurrent_run_in_progress"
        run.triggered_by = "dedup"
        run.finished_at = now
        run.result_summary = {
            "execution_complete": False,
            "source_run_id": str(other.id),
            "reason": "concurrent_run_in_progress",
        }
        db.add(run)
        await db.commit()
        return other.id
    return None


async def _cancel_requested(db: AsyncSession, run: ModelEvalRun) -> bool:
    # ``refresh`` starts a transaction.  A model request can legitimately take
    # longer than PostgreSQL's ``idle_in_transaction_session_timeout``; leaving
    # this read transaction open while the gateway runs makes PostgreSQL kill
    # the connection and the eventual evidence commit fail with
    # PendingRollbackError.  Capture the last known value, then always finish
    # (or roll back) the tiny polling transaction before network I/O resumes.
    cancelled = bool(run.cancel_requested)
    refresh = getattr(db, "refresh", None)
    if refresh is not None:
        try:
            await refresh(run, attribute_names=["cancel_requested"])
            cancelled = bool(run.cancel_requested)
            await db.commit()
            return cancelled
        except Exception:
            await db.rollback()
    return cancelled


async def _persist_case_results(
    db: AsyncSession,
    *,
    run: ModelEvalRun,
    suites: list[dict],
    results: list[dict],
) -> None:
    case_ids = {
        case["case_key"]: case["id"]
        for suite in suites
        for case in suite.get("cases") or []
    }
    for result in results:
        case_id = case_ids.get(result.get("case_key"))
        if case_id is None:
            continue
        db.add(
            ModelEvalCaseResult(
                id=uuid.uuid4(),
                run_id=run.id,
                case_id=case_id,
                variant_seed=0,
                context_target_tokens=result.get("context_target_tokens"),
                provider_prompt_tokens=result.get("provider_prompt_tokens"),
                response_hash=result.get("response_hash"),
                score=result.get("score"),
                passed=result.get("passed"),
                grader_detail=result.get("grader_detail") or {},
                latency_ms=result.get("latency_ms"),
                first_token_ms=result.get("first_token_ms"),
                completion_tokens=result.get("completion_tokens"),
                tokens_per_second=result.get("tokens_per_second"),
                error_code=result.get("error_code"),
            )
        )


async def _persist_role_scores(
    db: AsyncSession,
    *,
    catalog: ModelCatalog,
    roles: dict[str, dict],
    evidence_key: str,
    source_run_id: uuid.UUID,
) -> None:
    rows = (
        await db.execute(
            select(ModelRoleScore).where(ModelRoleScore.model_catalog_id == catalog.id)
        )
    ).scalars().all()
    by_role = {row.agent_role: row for row in rows}
    for role in ROUTABLE_ROLES:
        evidence_role = qualification_role_for(role)
        detail = roles.get(evidence_role) or {}
        row = by_role.get(role)
        if row is None:
            row = ModelRoleScore(
                id=uuid.uuid4(),
                model_catalog_id=catalog.id,
                agent_role=role,
                score_version="v98",
            )
            db.add(row)
        score = detail.get("score")
        row.benchmark_score = float(score) if score is not None else None
        row.benchmark_evidence_key = evidence_key
        row.benchmark_source_run_id = source_run_id
        row.benchmark_passed = bool(detail.get("passed"))
        if row.composite_score is None and score is not None:
            row.composite_score = float(score)
        row.confidence = min(1.0, 0.5 + 0.05 * int(detail.get("sample_count") or 0))
        row.score_version = "v98"
        row.detail_json = {
            **(row.detail_json or {}),
            "qualification": {
                **detail,
                "evidence_role": evidence_role,
                "reused_for_auxiliary_role": evidence_role != role,
                "evidence_key": evidence_key,
                "source_run_id": str(source_run_id),
                "evaluator_revision": ABILITY_EVALUATOR_REVISION,
            },
        }


def _ability_prior(run: ModelEvalRun | None) -> dict | None:
    if run is None:
        return None
    summary = run.result_summary or {}
    return {
        "identity_hash": run.ability_identity_hash,
        "suite_hash": run.ability_suite_hash,
        "evaluator_revision": run.ability_evaluator_revision,
        "source_run_id": str(run.id),
        "status": run.status,
        "overall": run.overall_score,
        "roles": summary.get("roles") or summary.get("role_scores") or {},
        "level": summary.get("level") or "none",
        "failed_cases": summary.get("failed_cases") or [],
    }


def _failed_case_diagnostics(results: list[dict]) -> list[dict]:
    """Return bounded diagnostics for synthetic failed qualification cases."""

    return [
        {
            "case_key": item.get("case_key"),
            "role": item.get("role"),
            "score": item.get("score"),
            "error_code": item.get("error_code"),
            "grader_detail": item.get("grader_detail") or {},
            "response_preview": str(item.get("response_preview") or "")[:1200],
            "latency_ms": item.get("latency_ms"),
        }
        for item in results
        if not item.get("passed")
    ]


def _context_prior(run: ModelEvalRun | None) -> dict | None:
    if run is None:
        return None
    summary = run.result_summary or {}
    return {
        "identity_hash": run.context_identity_hash,
        "context_suite_hash": run.context_suite_hash,
        "evaluator_revision": run.context_evaluator_revision,
        "source_run_id": str(run.id),
        "status": run.status,
        "declared_context_window": summary.get("declared_context_window"),
        "accepted_context_window": summary.get("accepted_context_window"),
        "effective_context_window": summary.get("effective_context_window"),
        "rung_results": summary.get("rung_results") or {},
        "position_robustness_score": summary.get("position_robustness_score"),
        "multi_hop_score": summary.get("multi_hop_score"),
        "instruction_retention_score": summary.get("instruction_retention_score"),
        "belief_boundary_score": summary.get("belief_boundary_score"),
    }


def _set_ability_run_fingerprint(
    run: ModelEvalRun,
    *,
    identity_hash: str,
    suite_hash: str,
    evaluation_key: str,
    force: bool,
) -> None:
    run.benchmark_revision = ABILITY_EVALUATOR_REVISION
    run.ability_identity_hash = identity_hash
    run.ability_suite_hash = suite_hash
    run.ability_evaluator_revision = ABILITY_EVALUATOR_REVISION
    run.ability_evaluation_key = evaluation_key
    run.force_requested = force


async def run_qualification(
    db: AsyncSession,
    run: ModelEvalRun,
    *,
    force: bool = False,
    gateway=None,
) -> dict:
    """Run/reuse ability evidence; a low score is reusable, an incomplete run is not."""

    gateway = gateway or _default_gateway
    catalog = (
        await db.execute(select(ModelCatalog).where(ModelCatalog.id == run.model_catalog_id))
    ).scalar_one_or_none()
    if catalog is None or not catalog.text_generation_eligible:
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.gateway_calls = 0
        run.result_summary = {
            "execution_complete": False,
            "error": "catalog_missing" if catalog is None else "non_text_model",
        }
        await db.commit()
        return {
            "status": "failed",
            "reason": run.result_summary["error"],
            "gateway_calls": 0,
            "reused": False,
        }

    await ensure_v98_suites(db)
    await db.flush()
    suites = await _load_v98_suites(db, mode="qualification")
    suite_hash = suite_aggregate_hash(suites)
    endpoint_hash = refresh_endpoint_identity(catalog)
    refresh_upstream_identity(catalog)
    identity = _identity_hash(catalog, endpoint_hash)
    evaluation_key = ability_evaluation_key(identity, suite_hash)
    prior_run = await _latest_source_run(
        db,
        catalog_id=catalog.id,
        mode="qualification",
        current_run_id=run.id,
        evaluation_key=evaluation_key,
    )
    prior = _ability_prior(prior_run)
    decision = decide_ability_reuse_with_parts(
        prior_identity_hash=(prior or {}).get("identity_hash"),
        prior_suite_hash=(prior or {}).get("suite_hash"),
        prior_rev=(prior or {}).get("evaluator_revision"),
        prior_source_run_id=(prior or {}).get("source_run_id"),
        prior_status=(prior or {}).get("status"),
        identity_hash=identity,
        suite_hash=suite_hash,
        force=force,
    )
    _set_ability_run_fingerprint(
        run,
        identity_hash=identity,
        suite_hash=suite_hash,
        evaluation_key=evaluation_key,
        force=force,
    )

    if decision.reuse and prior_run is not None:
        now = datetime.now(timezone.utc)
        roles = (prior_run.result_summary or {}).get("roles") or {}
        level = (prior_run.result_summary or {}).get("level") or "none"
        run.status = "succeeded"
        run.started_at = now
        run.finished_at = now
        run.overall_score = prior_run.overall_score
        run.confidence = prior_run.confidence
        run.ability_source_run_id = prior_run.id
        run.reuse_reason = "cache_hit"
        run.triggered_by = "cache_hit"
        run.gateway_calls = 0
        run.result_summary = {
            "execution_complete": True,
            "reused": True,
            "source_run_id": str(prior_run.id),
            "overall": prior_run.overall_score,
            "roles": roles,
            "level": level,
            "failed_cases": (prior_run.result_summary or {}).get("failed_cases") or [],
        }
        catalog.ability_evaluation_key = evaluation_key
        catalog.ability_identity_hash = identity
        catalog.ability_suite_hash = suite_hash
        catalog.ability_evaluator_revision = ABILITY_EVALUATOR_REVISION
        catalog.ability_reuse_reason = "cache_hit"
        catalog.ability_source_run_id = prior_run.id
        await _persist_role_scores(
            db,
            catalog=catalog,
            roles=roles,
            evidence_key=evaluation_key,
            source_run_id=prior_run.id,
        )
        await db.commit()
        return {
            "status": "succeeded",
            "execution_complete": True,
            "level": level,
            "overall": prior_run.overall_score,
            "roles": roles,
            "reused": True,
            "reuse_reason": "cache_hit",
            "changed_fields": [],
            "source_run_id": str(prior_run.id),
            "ability_evaluation_key": evaluation_key,
            "identity_hash": identity,
            "suite_hash": suite_hash,
            "evaluator_revision": ABILITY_EVALUATOR_REVISION,
            "gateway_calls": 0,
            "triggered_by": "cache_hit",
            "failed_cases": (prior_run.result_summary or {}).get("failed_cases") or [],
        }

    # v6 changes only the aggregation of already persisted deterministic case
    # scores: role cases remain mandatory, while the shared core uses an
    # aggregate floor.  No prompt, case grader, model identity or suite content
    # changed from v5, so derive a new immutable source run without another LLM
    # call and clone the case-result audit trail to that source.
    if (
        not force
        and prior_run is not None
        and prior_run.ability_evaluator_revision == "v98-ability-5"
        and ABILITY_EVALUATOR_REVISION == "v98-ability-6"
        and decision.changed_fields == ["evaluator_revision"]
    ):
        now = datetime.now(timezone.utc)
        prior_summary = prior_run.result_summary or {}
        roles = reaggregate_qualification_roles(
            prior_summary.get("roles") or {},
            execution_complete=True,
        )
        qualified_roles = [
            role for role, detail in roles.items() if detail.get("passed")
        ]
        prior_cases = (
            await db.execute(
                select(ModelEvalCaseResult).where(
                    ModelEvalCaseResult.run_id == prior_run.id
                )
            )
        ).scalars().all()
        for item in prior_cases:
            db.add(
                ModelEvalCaseResult(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    case_id=item.case_id,
                    variant_seed=item.variant_seed,
                    context_target_tokens=item.context_target_tokens,
                    provider_prompt_tokens=item.provider_prompt_tokens,
                    response_hash=item.response_hash,
                    score=item.score,
                    passed=item.passed,
                    grader_detail=item.grader_detail,
                    latency_ms=item.latency_ms,
                    first_token_ms=item.first_token_ms,
                    completion_tokens=item.completion_tokens,
                    tokens_per_second=item.tokens_per_second,
                    error_code=item.error_code,
                )
            )
        level = "role_qualified" if qualified_roles else "none"
        run.status = "succeeded"
        run.started_at = now
        run.finished_at = now
        run.overall_score = prior_run.overall_score
        run.confidence = prior_run.confidence
        run.ability_source_run_id = None
        run.reuse_reason = "aggregation_reuse"
        run.triggered_by = "evaluator_aggregation"
        run.gateway_calls = 0
        run.result_summary = {
            "execution_complete": True,
            "reused": True,
            "derived_from_source_run_id": str(prior_run.id),
            "overall": prior_run.overall_score,
            "roles": roles,
            "qualified_roles": qualified_roles,
            "level": level,
            "case_count": len(prior_cases),
            "failed_cases": prior_summary.get("failed_cases") or [],
        }
        catalog.ability_evaluation_key = evaluation_key
        catalog.ability_identity_hash = identity
        catalog.ability_suite_hash = suite_hash
        catalog.ability_evaluator_revision = ABILITY_EVALUATOR_REVISION
        catalog.ability_reuse_reason = "aggregation_reuse"
        catalog.ability_source_run_id = run.id
        catalog.ability_completed_at = now
        catalog.last_certified_at = now
        catalog.benchmark_revision = ABILITY_EVALUATOR_REVISION
        catalog.certification_level = level
        catalog.certification_confidence = run.confidence
        catalog.evaluation_status = "qualified" if qualified_roles else "evaluated"
        await _persist_role_scores(
            db,
            catalog=catalog,
            roles=roles,
            evidence_key=evaluation_key,
            source_run_id=run.id,
        )
        await db.commit()
        return {
            "status": "succeeded",
            "execution_complete": True,
            "level": level,
            "overall": prior_run.overall_score,
            "roles": roles,
            "qualified_roles": qualified_roles,
            "reused": True,
            "reuse_reason": "aggregation_reuse",
            "changed_fields": decision.changed_fields,
            "source_run_id": str(run.id),
            "previous_source_run_id": str(prior_run.id),
            "ability_evaluation_key": evaluation_key,
            "identity_hash": identity,
            "suite_hash": suite_hash,
            "evaluator_revision": ABILITY_EVALUATOR_REVISION,
            "gateway_calls": 0,
            "triggered_by": "evaluator_aggregation",
            "failed_cases": prior_summary.get("failed_cases") or [],
        }

    run.reuse_reason = decision.reason
    run.triggered_by = "force" if force else decision.reason
    claimed_by = await _claim_run(
        db,
        catalog_id=catalog.id,
        mode="qualification",
        run=run,
    )
    if claimed_by is not None:
        return {
            "status": "in_progress",
            "reused": False,
            "reuse_reason": "concurrent_run_in_progress",
            "changed_fields": decision.changed_fields,
            "source_run_id": str(claimed_by),
            "ability_evaluation_key": evaluation_key,
            "gateway_calls": 0,
            "triggered_by": "dedup",
        }

    try:
        result = await run_qualification_core(
            catalog=_catalog_payload(catalog),
            suites=suites,
            gateway=gateway,
            prior=prior,
            force=force,
            endpoint_identity_hash=endpoint_hash,
            cancel_check=lambda: _cancel_requested(db, run),
        )
    except Exception as exc:
        result = {
            "status": "failed",
            "execution_complete": False,
            "error": f"evaluator_exception:{type(exc).__name__}",
            "reused": False,
            "reuse_reason": decision.reason,
            "changed_fields": decision.changed_fields,
            "gateway_calls": 0,
            "case_results": [],
            "roles": {},
            "overall": None,
            "level": "none",
            "triggered_by": "force" if force else decision.reason,
        }

    case_results = result.get("case_results") or []
    failed_cases = _failed_case_diagnostics(case_results)
    await _persist_case_results(db, run=run, suites=suites, results=case_results)
    now = datetime.now(timezone.utc)
    run.status = result["status"]
    run.finished_at = now
    run.overall_score = result.get("overall")
    run.gateway_calls = int(result.get("gateway_calls") or 0)
    run.reuse_reason = result.get("reuse_reason") or decision.reason
    run.triggered_by = result.get("triggered_by") or run.triggered_by
    run.confidence = min(1.0, 0.5 + 0.03 * len(result.get("case_results") or []))
    run.result_summary = {
        "execution_complete": bool(result.get("execution_complete")),
        "error": result.get("error"),
        "reused": False,
        "overall": result.get("overall"),
        "roles": result.get("roles") or {},
        "qualified_roles": result.get("qualified_roles") or [],
        "level": result.get("level") or "none",
        "case_count": len(result.get("case_results") or []),
        "failed_cases": failed_cases,
    }
    if result["status"] == "succeeded" and result.get("execution_complete"):
        run.ability_source_run_id = None
        catalog.ability_evaluation_key = evaluation_key
        catalog.ability_identity_hash = identity
        catalog.ability_suite_hash = suite_hash
        catalog.ability_evaluator_revision = ABILITY_EVALUATOR_REVISION
        catalog.ability_reuse_reason = decision.reason
        catalog.ability_source_run_id = run.id
        catalog.ability_completed_at = now
        catalog.last_certified_at = now
        catalog.benchmark_revision = ABILITY_EVALUATOR_REVISION
        catalog.certification_level = result.get("level") or "none"
        catalog.certification_confidence = run.confidence
        catalog.evaluation_status = (
            "qualified" if result.get("qualified_roles") else "evaluated"
        )
        await _persist_role_scores(
            db,
            catalog=catalog,
            roles=result.get("roles") or {},
            evidence_key=evaluation_key,
            source_run_id=run.id,
        )
        result["source_run_id"] = str(run.id)
    else:
        result["source_run_id"] = None
    result["previous_source_run_id"] = decision.source_run_id
    result["changed_fields"] = decision.changed_fields
    result["ability_evaluation_key"] = evaluation_key
    result["identity_hash"] = identity
    result["suite_hash"] = suite_hash
    result["evaluator_revision"] = ABILITY_EVALUATOR_REVISION
    result["failed_cases"] = failed_cases
    result.pop("case_results", None)
    await db.commit()
    return result


async def _context_profile_for(
    db: AsyncSession,
    catalog_id: uuid.UUID,
) -> ModelContextProfile | None:
    return (
        await db.execute(
            select(ModelContextProfile).where(ModelContextProfile.model_catalog_id == catalog_id)
        )
    ).scalar_one_or_none()


async def _capability_for(
    db: AsyncSession,
    catalog_id: uuid.UUID,
) -> ModelCapabilityProfile | None:
    return (
        await db.execute(
            select(ModelCapabilityProfile).where(
                ModelCapabilityProfile.model_catalog_id == catalog_id
            )
        )
    ).scalar_one_or_none()


async def _write_context_profile(
    db: AsyncSession,
    *,
    catalog: ModelCatalog,
    capability: ModelCapabilityProfile | None,
    result: dict,
    source_run_id: uuid.UUID,
    completed_at: datetime,
) -> ModelContextProfile:
    profile = await _context_profile_for(db, catalog.id)
    if profile is None:
        profile = ModelContextProfile(id=uuid.uuid4(), model_catalog_id=catalog.id)
        db.add(profile)
    profile.declared_context_window = result.get("declared_context_window")
    profile.accepted_context_window = result.get("accepted_context_window")
    profile.effective_context_window = result.get("effective_context_window")
    profile.position_robustness_score = result.get("position_robustness_score")
    profile.multi_hop_score = result.get("multi_hop_score")
    profile.instruction_retention_score = result.get("instruction_retention_score")
    profile.belief_boundary_score = result.get("belief_boundary_score")
    profile.last_verified_at = completed_at
    profile.benchmark_revision = CONTEXT_EVALUATOR_REVISION
    profile.confidence = min(1.0, 0.5 + 0.08 * len(result.get("rung_results") or {}))
    profile.context_evaluation_key = result["context_evaluation_key"]
    profile.context_identity_hash = result["context_identity_hash"]
    profile.context_suite_hash = result["context_suite_hash"]
    profile.context_evaluator_revision = CONTEXT_EVALUATOR_REVISION
    profile.context_source_run_id = source_run_id
    if capability is None:
        capability = ModelCapabilityProfile(
            id=uuid.uuid4(),
            model_catalog_id=catalog.id,
            capability_source="benchmark",
        )
        db.add(capability)
    if result.get("declared_context_window") is not None:
        capability.declared_context_window = result.get("declared_context_window")
    capability.accepted_context_window = result.get("accepted_context_window")
    capability.effective_context_window = result.get("effective_context_window")
    capability.context_measurement_confidence = profile.confidence
    return profile


async def run_context_ladder(
    db: AsyncSession,
    run: ModelEvalRun,
    catalog: ModelCatalog | None = None,
    *,
    force: bool = False,
    gateway=None,
) -> dict:
    """Run/reuse the real context ladder; supports the legacy catalog argument."""

    gateway = gateway or _default_gateway
    if catalog is None:
        catalog = (
            await db.execute(select(ModelCatalog).where(ModelCatalog.id == run.model_catalog_id))
        ).scalar_one_or_none()
    if catalog is None or not catalog.text_generation_eligible:
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.gateway_calls = 0
        run.result_summary = {
            "execution_complete": False,
            "error": "catalog_missing" if catalog is None else "non_text_model",
        }
        await db.commit()
        return {
            "status": "failed",
            "reason": run.result_summary["error"],
            "gateway_calls": 0,
            "reused": False,
        }

    await ensure_v98_suites(db)
    await db.flush()
    suites = await _load_v98_suites(db, mode="context_ladder")
    suite_hash = suite_aggregate_hash(suites)
    endpoint_hash = refresh_endpoint_identity(catalog)
    refresh_upstream_identity(catalog)
    identity = _identity_hash(catalog, endpoint_hash)
    evaluation_key = context_evaluation_key(identity, suite_hash)
    capability = await _capability_for(db, catalog.id)
    declared = None
    if capability is not None:
        declared = capability.declared_context_window or capability.context_window
    prior_run = await _latest_source_run(
        db,
        catalog_id=catalog.id,
        mode="context_ladder",
        current_run_id=run.id,
        evaluation_key=evaluation_key,
    )
    prior = _context_prior(prior_run)
    decision = decide_context_reuse_with_parts(
        prior_identity_hash=(prior or {}).get("identity_hash"),
        prior_context_suite_hash=(prior or {}).get("context_suite_hash"),
        prior_rev=(prior or {}).get("evaluator_revision"),
        prior_source_run_id=(prior or {}).get("source_run_id"),
        prior_status=(prior or {}).get("status"),
        identity_hash=identity,
        context_suite_hash=suite_hash,
        force=force,
    )
    run.context_evaluation_key = evaluation_key
    run.context_identity_hash = identity
    run.context_suite_hash = suite_hash
    run.context_evaluator_revision = CONTEXT_EVALUATOR_REVISION
    run.force_requested = force

    if decision.reuse and prior_run is not None:
        now = datetime.now(timezone.utc)
        summary = prior_run.result_summary or {}
        run.status = "succeeded"
        run.started_at = now
        run.finished_at = now
        run.context_source_run_id = prior_run.id
        run.reuse_reason = "cache_hit"
        run.triggered_by = "cache_hit"
        run.gateway_calls = 0
        run.confidence = prior_run.confidence
        run.result_summary = {
            **summary,
            "execution_complete": True,
            "reused": True,
            "source_run_id": str(prior_run.id),
        }
        catalog.context_evaluation_key = evaluation_key
        catalog.context_identity_hash = identity
        catalog.context_suite_hash = suite_hash
        catalog.context_evaluator_revision = CONTEXT_EVALUATOR_REVISION
        catalog.context_source_run_id = prior_run.id
        source_completed_at = prior_run.finished_at or catalog.context_completed_at or now
        await _write_context_profile(
            db,
            catalog=catalog,
            capability=capability,
            result={
                **summary,
                "context_evaluation_key": evaluation_key,
                "context_identity_hash": identity,
                "context_suite_hash": suite_hash,
            },
            source_run_id=prior_run.id,
            completed_at=source_completed_at,
        )
        if catalog.context_completed_at is None:
            catalog.context_completed_at = source_completed_at
        await db.commit()
        return {
            "status": "succeeded",
            "execution_complete": True,
            "declared": summary.get("declared_context_window"),
            "accepted": summary.get("accepted_context_window"),
            "effective": summary.get("effective_context_window"),
            "declared_context_window": summary.get("declared_context_window"),
            "accepted_context_window": summary.get("accepted_context_window"),
            "effective_context_window": summary.get("effective_context_window"),
            "rung_results": summary.get("rung_results") or {},
            "reused": True,
            "reuse_reason": "cache_hit",
            "changed_fields": [],
            "source_run_id": str(prior_run.id),
            "context_evaluation_key": evaluation_key,
            "context_identity_hash": identity,
            "context_suite_hash": suite_hash,
            "evaluator_revision": CONTEXT_EVALUATOR_REVISION,
            "gateway_calls": 0,
            "triggered_by": "cache_hit",
        }

    run.reuse_reason = decision.reason
    run.triggered_by = "force" if force else decision.reason
    claimed_by = await _claim_run(
        db,
        catalog_id=catalog.id,
        mode="context_ladder",
        run=run,
    )
    if claimed_by is not None:
        return {
            "status": "in_progress",
            "reused": False,
            "reuse_reason": "concurrent_run_in_progress",
            "changed_fields": decision.changed_fields,
            "source_run_id": str(claimed_by),
            "context_evaluation_key": evaluation_key,
            "gateway_calls": 0,
            "triggered_by": "dedup",
        }

    try:
        result = await run_context_ladder_core(
            catalog=_catalog_payload(catalog),
            suites=suites,
            gateway=gateway,
            prior=prior,
            force=force,
            declared_context_window=declared,
            endpoint_identity_hash=endpoint_hash,
            cancel_check=lambda: _cancel_requested(db, run),
        )
    except Exception as exc:
        result = {
            "status": "failed",
            "execution_complete": False,
            "error": f"evaluator_exception:{type(exc).__name__}",
            "reused": False,
            "reuse_reason": decision.reason,
            "changed_fields": decision.changed_fields,
            "gateway_calls": 0,
            "rung_results": {},
            "case_results": [],
            "triggered_by": "force" if force else decision.reason,
            "context_evaluation_key": evaluation_key,
            "context_identity_hash": identity,
            "context_suite_hash": suite_hash,
        }

    await _persist_case_results(db, run=run, suites=suites, results=result.get("case_results") or [])
    now = datetime.now(timezone.utc)
    run.status = result["status"]
    run.finished_at = now
    run.gateway_calls = int(result.get("gateway_calls") or 0)
    run.reuse_reason = result.get("reuse_reason") or decision.reason
    run.triggered_by = result.get("triggered_by") or run.triggered_by
    rung_values = [
        detail.get("accuracy")
        for detail in (result.get("rung_results") or {}).values()
        if detail.get("accuracy") is not None
    ]
    run.overall_score = round(sum(rung_values) / len(rung_values), 1) if rung_values else None
    run.confidence = min(1.0, 0.5 + 0.08 * len(result.get("rung_results") or {}))
    run.result_summary = {
        "execution_complete": bool(result.get("execution_complete")),
        "error": result.get("error"),
        "reused": False,
        "declared_context_window": result.get("declared_context_window"),
        "accepted_context_window": result.get("accepted_context_window"),
        "effective_context_window": result.get("effective_context_window"),
        "rung_results": result.get("rung_results") or {},
        "position_robustness_score": result.get("position_robustness_score"),
        "multi_hop_score": result.get("multi_hop_score"),
        "instruction_retention_score": result.get("instruction_retention_score"),
        "belief_boundary_score": result.get("belief_boundary_score"),
        "case_count": len(result.get("case_results") or []),
    }
    if result["status"] == "succeeded" and result.get("execution_complete"):
        run.context_source_run_id = None
        await _write_context_profile(
            db,
            catalog=catalog,
            capability=capability,
            result=result,
            source_run_id=run.id,
            completed_at=now,
        )
        catalog.context_evaluation_key = evaluation_key
        catalog.context_identity_hash = identity
        catalog.context_suite_hash = suite_hash
        catalog.context_evaluator_revision = CONTEXT_EVALUATOR_REVISION
        catalog.context_source_run_id = run.id
        catalog.context_completed_at = now
        catalog.evaluation_status = (
            "context_verified" if result.get("effective_context_window") else "context_failed"
        )
        result["source_run_id"] = str(run.id)
    else:
        result["source_run_id"] = None
    result["previous_source_run_id"] = decision.source_run_id
    result["changed_fields"] = decision.changed_fields
    result["context_evaluation_key"] = evaluation_key
    result["context_identity_hash"] = identity
    result["context_suite_hash"] = suite_hash
    result["evaluator_revision"] = CONTEXT_EVALUATOR_REVISION
    result["declared"] = result.get("declared_context_window")
    result["accepted"] = result.get("accepted_context_window")
    result["effective"] = result.get("effective_context_window")
    result["by_rung"] = result.get("rung_results") or {}
    result.pop("case_results", None)
    await db.commit()
    return result


async def get_catalog_evidence_state(
    db: AsyncSession,
    catalog: ModelCatalog,
    *,
    suite_hashes: tuple[str | None, str | None] | None = None,
) -> dict:
    """Compute live valid/stale/missing state and verify persisted source runs."""

    endpoint_hash = derive_endpoint_identity_hash(catalog)
    if suite_hashes is None:
        ability_hash = await _ability_suite_hash(db)
        context_hash = await _context_suite_hash(db)
    else:
        ability_hash, context_hash = suite_hashes
    metadata = dict(catalog.metadata_json or {})
    state = current_evidence_state(
        provider=catalog.provider,
        model_id=catalog.model_id,
        model_kind=catalog.model_kind,
        endpoint_identity_hash=endpoint_hash,
        owned_by=metadata.get("owned_by"),
        created=metadata.get("created"),
        upstream_revision=metadata.get("upstream_revision"),
        ability_suite_hash=ability_hash,
        context_suite_hash=context_hash,
        catalog_ability_evaluation_key=catalog.ability_evaluation_key,
        catalog_ability_identity_hash=catalog.ability_identity_hash,
        catalog_ability_suite_hash=catalog.ability_suite_hash,
        catalog_ability_evaluator_revision=catalog.ability_evaluator_revision,
        catalog_context_evaluation_key=catalog.context_evaluation_key,
        catalog_context_identity_hash=catalog.context_identity_hash,
        catalog_context_suite_hash=catalog.context_suite_hash,
        catalog_context_evaluator_revision=catalog.context_evaluator_revision,
    )

    async def verify_source(kind: str) -> None:
        descriptor = state[kind]
        if descriptor["state"] != "valid":
            return
        source_id = getattr(catalog, f"{kind}_source_run_id", None)
        if source_id is None:
            descriptor.update(state="missing", reason=f"{kind}_source_missing", changed_fields=["source_run"])
            return
        source = await db.get(ModelEvalRun, source_id)
        key_attr = f"{kind}_evaluation_key"
        if (
            source is None
            or source.status != "succeeded"
            or not bool((source.result_summary or {}).get("execution_complete", True))
            or getattr(source, key_attr, None) != state[key_attr]
        ):
            descriptor.update(state="stale", reason=f"{kind}_source_invalid", changed_fields=["source_run"])
        descriptor["source_run_id"] = str(source_id)
        descriptor["completed_at"] = getattr(catalog, f"{kind}_completed_at", None)

    await verify_source("ability")
    await verify_source("context")
    role_rows = (
        await db.execute(
            select(ModelRoleScore).where(ModelRoleScore.model_catalog_id == catalog.id)
        )
    ).scalars().all()
    role_evidence = {}
    for role in ROUTABLE_ROLES:
        evidence_role = qualification_role_for(role)
        # The directly-qualified row is the source of truth.  This also makes
        # old one-time evidence immediately reusable after a new auxiliary role
        # is introduced; no retest is needed merely to create an alias row.
        row = next((item for item in role_rows if item.agent_role == evidence_role), None)
        current = bool(
            row
            and state["ability"]["state"] == "valid"
            and row.benchmark_evidence_key == state["ability_evaluation_key"]
            and row.benchmark_source_run_id == catalog.ability_source_run_id
        )
        role_evidence[role] = {
            "state": "valid" if current else "missing" if row is None else "stale",
            "score": row.benchmark_score if row else None,
            "passed": bool(row.benchmark_passed) if current else False,
            "evidence_key": row.benchmark_evidence_key if row else None,
            "source_run_id": str(row.benchmark_source_run_id) if row and row.benchmark_source_run_id else None,
            "evidence_role": evidence_role,
            "reused_for_auxiliary_role": evidence_role != role,
        }
    profile = await _context_profile_for(db, catalog.id)
    state["endpoint_identity_hash"] = endpoint_hash
    state["ability_suite_hash"] = ability_hash
    state["context_suite_hash"] = context_hash
    state["role_evidence"] = role_evidence
    state["context_profile"] = {
        "declared": profile.declared_context_window if profile else None,
        "accepted": profile.accepted_context_window if profile else None,
        "effective": profile.effective_context_window if profile else None,
        "position_robustness_score": profile.position_robustness_score if profile else None,
        "multi_hop_score": profile.multi_hop_score if profile else None,
        "instruction_retention_score": profile.instruction_retention_score if profile else None,
        "belief_boundary_score": profile.belief_boundary_score if profile else None,
    }
    return state
__all__ = [
    "ABILITY_EVALUATOR_REVISION",
    "CONTEXT_EVALUATOR_REVISION",
    "SuiteDefinitionDriftError",
    "_ability_suite_hash",
    "_context_suite_hash",
    "_suite_id",
    "ability_evaluation_key",
    "compute_endpoint_identity_hash",
    "context_evaluation_key",
    "current_evidence_state",
    "decide_ability_reuse_with_parts",
    "decide_context_reuse_with_parts",
    "ensure_v98_suites",
    "get_catalog_evidence_state",
    "grade_response",
    "model_identity_hash",
    "normalize_suite",
    "pick_ladder",
    "refresh_endpoint_identity",
    "refresh_upstream_identity",
    "run_context_ladder",
    "run_context_ladder_core",
    "run_qualification",
    "run_qualification_core",
    "seed_suites",
    "suite_aggregate_hash",
]
