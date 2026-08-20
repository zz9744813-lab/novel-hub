"""Unified Agent caller - v7.4 production P0 fixes.

P0-02: await merge + single short transaction for Run/Output/Usage/Context
P0-03: no db parameter; never hold session across LLM
P0-05: persist every AttemptRecord as route event + context package
"""
from __future__ import annotations

import uuid
import json
import logging
from types import SimpleNamespace
from datetime import datetime, timezone

from app.database import async_session_factory
from app.gateway.model_gateway import stream_with_retry
from app.gateway.publish_pipeline import full_pipeline_async, PublishState
from app.prompts import PROMPTS, AGENT_TEMPERATURES, AGENT_IS_JSON
from app.models import AgentRun, AgentRunOutput, LlmUsageEvent
from app.v74_utils import (
    ModelBindingService,
    record_model_route,
    save_context_package,
)

logger = logging.getLogger("novelforge.agents")

STRICT_ROLES = {
    "draft_writer",
    "chapter_planner",
    "review_agent",
    "state_extractor",
    "local_rewrite_editor",
    "outline_parser",
    "blank_planner",
    "query_planner",
    "evidence_ranker",
    "aileak_judge",
    "drift_audit",
}


def _prompt_variables(user_content: str, assembly_manifest: dict | None) -> dict:
    """Build the explicit variable package used by Prompt Studio templates."""
    values: dict = {
        "user_content": user_content,
        "user_instruction": user_content,
        "assembly_manifest": assembly_manifest or {},
        "context_package": assembly_manifest or {},
    }
    try:
        decoded = json.loads(user_content)
    except (TypeError, json.JSONDecodeError):
        return values
    if isinstance(decoded, dict):
        values.update(decoded)
        values.setdefault("context_package", decoded.get("context_package", assembly_manifest or {}))
    return values


class ModelBindingMissingError(RuntimeError):
    """Raised when a required agent has no DB model binding."""


async def _resolve_model(
    agent_role: str,
    book_id: uuid.UUID,
    overrides: dict | None,
) -> tuple[str, str, str | None]:
    # Model/provider selection is audit-controlled by DB bindings. Overrides may
    # tune generation parameters, but cannot bypass the lock or its fallback.
    async with async_session_factory() as db:
        svc = ModelBindingService(db)
        binding = await svc.get_binding(agent_role, book_id)
        if binding:
            return binding.provider, binding.primary_model, binding.fallback_model

    raise ModelBindingMissingError(
        f"No model binding for agent_role={agent_role} book_id={book_id}. "
        "Configure it in the Model Binding panel before running production agents."
    )


async def call_agent(
    *,
    book_id: uuid.UUID,
    agent_role: str,
    user_content: str,
    chapter_id: uuid.UUID | None = None,
    scene_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    overrides: dict | None = None,
    assembly_manifest: dict | None = None,
    l4_refs: list | None = None,
    l1_refs: list | None = None,
    l2_refs: list | None = None,
    l3_refs: list | None = None,
    genre_profile_id: uuid.UUID | None = None,
) -> tuple[AgentRun, str | dict | None, dict]:
    """Call an agent without holding a caller-provided DB session.

    Returns (run, publishable, metadata). Run is detached ORM after final query.
    """
    # v8 Prompt Studio: try active template first
    prompt_config = None
    template_obj = None
    from app.models.tables import PromptTemplateVersion
    from sqlalchemy import select
    try:
        async with async_session_factory() as db_tpl:
            template_obj = (await db_tpl.execute(
                select(PromptTemplateVersion)
                .where(PromptTemplateVersion.agent_role == agent_role)
                .where(PromptTemplateVersion.status == "active")
                .where(PromptTemplateVersion.activated_at.isnot(None))
                .order_by(PromptTemplateVersion.version.desc())
                .limit(1)
            )).scalar_one_or_none()
            if template_obj:
                prompt_config = {
                    "version": f"v{template_obj.version}",
                    "system_prompt": template_obj.system_prompt or "",
                    "user_prompt_template": template_obj.user_prompt_template or "",
                    "template_id": template_obj.id,
                    "template_version": template_obj.version,
                    "output_schema": getattr(template_obj, "output_schema", None) or getattr(template_obj, "compiled_schema", None),
                }
                logger.info("using PromptStudio template %s v%s for %s", template_obj.template_key, template_obj.version, agent_role)
    except Exception as e:
        logger.warning("PromptStudio lookup failed, fallback to PROMPTS: %s", e)

    if prompt_config is None:
        if agent_role not in PROMPTS:
            base_prompt = PROMPTS.get("query_planner") or next(iter(PROMPTS.values()))
            prompt_config = {
                **base_prompt,
                "version": f"{agent_role}-v1",
                "system_prompt": base_prompt.get("system_prompt", ""),
            }
        else:
            prompt_config = {**PROMPTS[agent_role]}
        prompt_config.setdefault("template_id", f"builtin:{agent_role}")
        prompt_config.setdefault("template_version", prompt_config.get("version", "v1"))

    # Runtime compilation is shared by built-in and Prompt Studio templates.
    # A Studio template's User Template is part of the model request, not UI-only metadata.
    from app.prompt_runtime import compile_prompt, prompt_snapshot
    prompt_config.setdefault("user_prompt_template", "{{user_content}}")
    runtime_template = SimpleNamespace(
        id=prompt_config.get("template_id", f"builtin:{agent_role}"),
        version=prompt_config.get("template_version", prompt_config.get("version", "v1")),
        system_prompt=prompt_config.get("system_prompt", ""),
        user_prompt_template=prompt_config.get("user_prompt_template", "{{user_content}}"),
    )
    compiled_prompt = compile_prompt(runtime_template, _prompt_variables(user_content, assembly_manifest))

    temperature = (overrides or {}).get("temperature", AGENT_TEMPERATURES.get(agent_role, 0.7))
    is_json = AGENT_IS_JSON.get(agent_role, False)

    # PR-05 / B-09: structured roles use Pydantic contract → json_schema (strict)
    response_format = None
    response_contract = None
    if is_json:
        from app.contracts.agents import (
            get_contract,
            response_format_for_role,
        )
        response_contract = get_contract(agent_role)
        response_format = response_format_for_role(agent_role, strict=True)
        if response_format is None:
            # fallback to prompt output_schema if no contract registered
            output_schema = prompt_config.get("output_schema") if isinstance(prompt_config, dict) else None
            if isinstance(output_schema, dict) and output_schema:
                schema = dict(output_schema)
                schema.setdefault("type", "object")
                if schema.get("type") == "object" and "additionalProperties" not in schema:
                    schema["additionalProperties"] = False
                response_format = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": f"{agent_role}_v1".replace("-", "_")[:64],
                        "strict": True,
                        "schema": schema,
                    },
                }

    try:
        provider, model, fallback_model = await _resolve_model(agent_role, book_id, overrides)
    except ModelBindingMissingError as e:
        logger.error(str(e))
        run_id = uuid.uuid4()
        run = AgentRun(
            id=run_id,
            book_id=book_id,
            chapter_id=chapter_id,
            scene_id=scene_id,
            agent_role=agent_role,
            status="failed",
            prompt_version=prompt_config["version"],
            model_name="UNBOUND",
            idempotency_key=f"{agent_role}:{book_id}:{chapter_id}:{scene_id or ''}",
            parent_run_id=parent_run_id,
            completed_at=datetime.now(timezone.utc),
        )
        async with async_session_factory() as db_run:
            db_run.add(run)
            await db_run.commit()
        return run, None, {
            "error": "model_binding_missing",
            "block_reason": str(e),
            "agent_role": agent_role,
        }

    system_prompt = compiled_prompt.system_text
    rendered_prompt = compiled_prompt.rendered_text

    run_id = uuid.uuid4()
    run = AgentRun(
        id=run_id,
        book_id=book_id,
        chapter_id=chapter_id,
        scene_id=scene_id,
        agent_role=agent_role,
        status="running",
        prompt_version=prompt_config["version"],
        model_name=model,
        idempotency_key=f"{agent_role}:{book_id}:{chapter_id}:{scene_id or ''}",
        parent_run_id=parent_run_id,
    )

    # P1 COST-001: Chinese-safe token estimate
    from app.token_estimate import safe_token_estimate

    used_est = safe_token_estimate(rendered_prompt, agent_role=agent_role)
    if assembly_manifest:
        default_manifest = dict(assembly_manifest)
        # Always stamp measured used for this attempt; never block on budget
        budget = dict(default_manifest.get("budget") or {})
        budget.setdefault("mode", "record_only")
        budget["used"] = used_est
        budget.setdefault("input_budget", budget.get("max_context", 128000) - budget.get("reserved_output", 10000))
        if budget.get("input_budget") and used_est > int(budget["input_budget"]):
            budget["overflow_advisory"] = True
        else:
            budget.setdefault("overflow_advisory", False)
        default_manifest["budget"] = budget
        default_manifest["used_tokens"] = used_est
        default_manifest["budget_mode"] = "record_only"
    else:
        default_manifest = {
            "entries": [],
            "excluded_entries": [],
            "used_tokens": used_est,
            "budget_mode": "record_only",
            "budget": {
                "max_context": 128000,
                "reserved_output": 10000,
                "input_budget": 118000,
                "used": used_est,
                "mode": "record_only",
                "overflow_advisory": False,
            },
        }

    # Phase 1: create run only (attempt packages written after stream with real audit)
    async with async_session_factory() as db_run:
        db_run.add(run)
        await db_run.commit()

    # Phase 2: LLM — no session held
    result = await stream_with_retry(
        system_prompt=system_prompt,
        user_content=compiled_prompt.user_text,
        model=model,
        temperature=temperature,
        provider=provider,
        fallback_model=fallback_model,
        response_format=response_format,
    )

    attempts = list(getattr(result, "attempts", None) or [])
    if not attempts:
        # single synthetic attempt if gateway returned without records
        from app.gateway.model_gateway import AttemptRecord
        attempts = [
            AttemptRecord(
                attempt_no=result.attempt or 1,
                provider=result.actual_provider or result.provider_used or provider,
                model=result.actual_model or model,
                route_type="primary",
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                latency_ms=result.latency_ms,
                success=bool(result.final_content and not result.error),
                error_code=result.error,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
            )
        ]

    attempt_compiled_prompts = {
        att.attempt_no: compiled_prompt for att in attempts
    }

    successful = next((a for a in attempts if a.success), None)
    final_attempt = successful or attempts[-1]
    actual_provider = final_attempt.provider
    actual_model = final_attempt.model
    attempt_no = final_attempt.attempt_no
    route_type = final_attempt.route_type

    # Persist every attempt: route event + context package
    async with async_session_factory() as db_att:
        for att in attempts:
            await record_model_route(
                db=db_att,
                run_id=run_id,
                attempt_no=att.attempt_no,
                agent_role=agent_role,
                configured_provider=provider,
                configured_model=model,
                actual_provider=att.provider,
                actual_model=att.model,
                route_type=att.route_type,
                reason=att.error_code,
            )
            await save_context_package(
                db=db_att,
                run_id=run_id,
                attempt_no=att.attempt_no,
                book_id=book_id,
                agent_role=agent_role,
                provider=att.provider,
                model=att.model,
                prompt_version=prompt_config["version"],
                system_prompt=attempt_compiled_prompts[att.attempt_no].system_text,
                rendered_prompt=attempt_compiled_prompts[att.attempt_no].rendered_text,
                request_params={
                    "temperature": temperature,
                    "is_json": is_json,
                    "route_type": att.route_type,
                    "success": att.success,
                    "response_format": bool(response_format),
                },
                assembly_manifest=default_manifest,
                l4_refs=l4_refs or [],
                l1_refs=l1_refs or [],
                l2_refs=l2_refs or [],
                l3_refs=l3_refs or [],
                genre_profile_id=genre_profile_id,
                prompt_snapshot=prompt_snapshot(attempt_compiled_prompts[att.attempt_no]),
                chapter_id=chapter_id,
                scene_id=scene_id,
            )
        await db_att.commit()

    publishable, state, meta = await full_pipeline_async(
        result,
        is_json=is_json,
        agent_role=agent_role,
        book_id=book_id,
        response_contract=(response_contract or agent_role) if is_json else None,
    )

    # PR-05 §9.3: one repair attempt on schema/pydantic failure (same model)
    br = (meta or {}).get("block_reason") or ""
    if (
        is_json
        and publishable is None
        and (
            br.startswith("pydantic_validation_failed")
            or br == "json_parse_failed"
            or br == "contract_non_object"
        )
        and result.final_content
    ):
        logger.warning("schema fail for %s (%s); one repair attempt", agent_role, br)
        repair_user = (
            user_content
            + "\n\n[SCHEMA_REPAIR]\n"
            + "Previous output failed validation:\n"
            + br
            + "\nRaw (truncated):\n"
            + (result.final_content or "")[:2000]
            + "\nReturn ONLY valid JSON matching the required schema. No markdown."
        )
        repair_template = SimpleNamespace(
            id=f"{prompt_config.get('template_id', f'builtin:{agent_role}')}:repair",
            version=prompt_config.get("template_version", prompt_config.get("version", "v1")),
            system_prompt=system_prompt,
            user_prompt_template="{{user_content}}",
        )
        repair_compiled_prompt = compile_prompt(
            repair_template,
            {
                "user_content": repair_user,
                "user_instruction": repair_user,
                "assembly_manifest": assembly_manifest or {},
                "context_package": assembly_manifest or {},
            },
        )
        repair_result = await stream_with_retry(
            system_prompt=repair_compiled_prompt.system_text,
            user_content=repair_compiled_prompt.user_text,
            model=model,
            temperature=min(float(temperature), 0.2),
            provider=provider,
            fallback_model=fallback_model,
            response_format=response_format,
        )
        # Keep repair attempts globally ordered so route/context rows are unique.
        if getattr(repair_result, "attempts", None):
            repair_attempts = []
            attempt_offset = max((a.attempt_no for a in attempts), default=0)
            for repair_attempt in repair_result.attempts:
                repair_attempt.attempt_no += attempt_offset
                repair_attempts.append(repair_attempt)
                attempt_compiled_prompts[repair_attempt.attempt_no] = repair_compiled_prompt
            attempts = list(attempts) + repair_attempts
            successful = next((a for a in attempts if a.success), None)
            final_attempt = successful or attempts[-1]
            actual_provider = final_attempt.provider
            actual_model = final_attempt.model
            attempt_no = final_attempt.attempt_no
            route_type = final_attempt.route_type
        result = repair_result
        result.attempts = attempts
        result.attempt = final_attempt.attempt_no
        result.successful_attempt_no = next(
            (a.attempt_no for a in attempts if a.success), None
        )
        publishable, state, meta = await full_pipeline_async(
            result,
            is_json=is_json,
            agent_role=agent_role,
            book_id=book_id,
            response_contract=response_contract or agent_role,
        )
        if publishable is None:
            meta = {**(meta or {}), "schema_repair_attempted": True}
        else:
            meta = {**(meta or {}), "schema_repair_succeeded": True}

    new_status = "completed" if publishable is not None else "failed"
    completed_at = datetime.now(timezone.utc)
    publish_state = state.value if publishable is not None else "blocked"
    block_reason = meta.get("block_reason")

    raw_response_summary = {
        "provider": actual_provider,
        "model": actual_model,
        "attempt": attempt_no,
        "route_type": route_type,
        "successful_attempt_no": result.successful_attempt_no,
        "error": result.error,
        "latency_ms": result.latency_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "reasoning_tokens": result.reasoning_tokens,
        "attempts": [
            {
                "attempt_no": a.attempt_no,
                "provider": a.provider,
                "model": a.model,
                "route_type": a.route_type,
                "success": a.success,
                "error_code": a.error_code,
                "latency_ms": a.latency_ms,
            }
            for a in attempts
        ],
    }

    output = AgentRunOutput(
        run_id=run_id,
        book_id=book_id,
        agent_role=agent_role,
        provider=actual_provider,
        model_name=actual_model,
        raw_provider_response=raw_response_summary,
        reasoning_text=result.reasoning_text[:10000] if result.reasoning_text else None,
        final_content=result.final_content[:50000] if result.final_content else None,
        normalized_content=(
            json.dumps(publishable, ensure_ascii=False)
            if isinstance(publishable, dict)
            else publishable
        ),
        publishable_content=(
            json.dumps(publishable, ensure_ascii=False)
            if isinstance(publishable, dict)
            else publishable
        ),
        reasoning_detected=result.reasoning_detected,
        inline_leak_detected=result.inline_leak_detected,
        leak_status="checked" if state != PublishState.BLOCKED else "blocked",
        output_integrity=state.value if publishable else "blocked",
    )

    # INV-10: every real gateway attempt records its own Usage row.  The final
    # StreamResult counters are not sufficient when a repair/fallback occurred.
    usage_events = []
    usage_unknown = False
    for att in attempts:
        pt_u = int(att.prompt_tokens or 0)
        ct_u = int(att.completion_tokens or 0)
        attempt_unknown = not (att.prompt_tokens or att.completion_tokens)
        usage_unknown = usage_unknown or attempt_unknown
        usage_events.append(
            LlmUsageEvent(
                id=uuid.uuid4(),
                book_id=book_id,
                run_id=run_id,
                attempt_no=att.attempt_no,
                provider=att.provider or provider or "unknown",
                model_name=att.model or model or "unknown",
                prompt_tokens=pt_u,
                completion_tokens=ct_u,
                reasoning_tokens=0,
                total_tokens=pt_u + ct_u,
                latency_ms=int(att.latency_ms or 0),
            )
        )
    raw_response_summary["usage_status"] = "unknown" if usage_unknown else "known"
    raw_response_summary["usage_unknown"] = usage_unknown

    # Repair attempts were not known during the initial audit transaction.
    # Persist their route/context rows before closing the run.
    if len(attempts) > 0:
        async with async_session_factory() as db_repair_audit:
            existing_attempts = set()
            from sqlalchemy import select
            from app.models.tables import ModelRouteEvent

            existing_rows = await db_repair_audit.execute(
                select(ModelRouteEvent.attempt_no).where(ModelRouteEvent.run_id == run_id)
            )
            existing_attempts = set(existing_rows.scalars().all())
            for att in attempts:
                if att.attempt_no in existing_attempts:
                    continue
                await record_model_route(
                    db=db_repair_audit,
                    run_id=run_id,
                    attempt_no=att.attempt_no,
                    agent_role=agent_role,
                    configured_provider=provider,
                    configured_model=model,
                    actual_provider=att.provider,
                    actual_model=att.model,
                    route_type=att.route_type,
                    reason=att.error_code,
                )
                await save_context_package(
                    db=db_repair_audit,
                    run_id=run_id,
                    attempt_no=att.attempt_no,
                    book_id=book_id,
                    agent_role=agent_role,
                    provider=att.provider,
                    model=att.model,
                    prompt_version=prompt_config["version"],
                    system_prompt=attempt_compiled_prompts[att.attempt_no].system_text,
                    rendered_prompt=attempt_compiled_prompts[att.attempt_no].rendered_text,
                    request_params={
                        "temperature": temperature,
                        "is_json": is_json,
                        "route_type": att.route_type,
                        "success": att.success,
                        "response_format": bool(response_format),
                        "schema_repair": att.attempt_no > max(existing_attempts or {0}),
                    },
                    assembly_manifest=default_manifest,
                    l4_refs=l4_refs or [],
                    l1_refs=l1_refs or [],
                    l2_refs=l2_refs or [],
                    l3_refs=l3_refs or [],
                    genre_profile_id=genre_profile_id,
                    prompt_snapshot=prompt_snapshot(attempt_compiled_prompts[att.attempt_no]),
                    chapter_id=chapter_id,
                    scene_id=scene_id,
                )
            await db_repair_audit.commit()

    # Phase 3: single short transaction for Output + Usage + Run status + package update
    async with async_session_factory() as db_out:
        from sqlalchemy import select, update
        from app.models.tables import AgentContextPackage

        managed_run = await db_out.merge(run)
        managed_run.status = new_status
        managed_run.completed_at = completed_at
        managed_run.model_name = actual_model

        db_out.add(output)
        db_out.add_all(usage_events)

        await db_out.execute(
            update(AgentContextPackage)
            .where(
                AgentContextPackage.run_id == run_id,
                AgentContextPackage.attempt_no == attempt_no,
            )
            .values(
                publish_state=publish_state,
                block_reason=block_reason,
                completed_at=completed_at,
                provider=actual_provider,
                model=actual_model,
            )
        )
        await db_out.commit()

        # Re-query run so caller sees DB truth
        refreshed = (
            await db_out.execute(select(AgentRun).where(AgentRun.id == run_id))
        ).scalar_one()
        # Detach attributes we need
        final_run = AgentRun(
            id=refreshed.id,
            book_id=refreshed.book_id,
            chapter_id=refreshed.chapter_id,
            scene_id=refreshed.scene_id,
            agent_role=refreshed.agent_role,
            status=refreshed.status,
            prompt_version=refreshed.prompt_version,
            model_name=refreshed.model_name,
            started_at=refreshed.started_at,
            completed_at=refreshed.completed_at,
            idempotency_key=refreshed.idempotency_key,
            parent_run_id=refreshed.parent_run_id,
        )
        if final_run.status not in ("completed", "failed"):
            raise RuntimeError(f"AgentRun {run_id} left in invalid status={final_run.status}")

    return final_run, publishable, {
        "reasoning_detected": result.reasoning_detected,
        "inline_leak_detected": result.inline_leak_detected,
        "error": result.error,
        "latency_ms": result.latency_ms,
        "attempt": attempt_no,
        "provider_used": actual_provider,
        "model_used": actual_model,
        "route_type": route_type,
        "block_reason": block_reason,
        "successful_attempt_no": result.successful_attempt_no,
        "attempts": raw_response_summary["attempts"],
        "usage_status": raw_response_summary.get("usage_status"),
        "usage_unknown": raw_response_summary.get("usage_unknown"),
        **meta,
    }
