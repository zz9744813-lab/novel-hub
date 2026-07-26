"""Unified Agent caller - v7.4 production P0 fixes.

P0-02: await merge + single short transaction for Run/Output/Usage/Context
P0-03: no db parameter; never hold session across LLM
P0-05: persist every AttemptRecord as route event + context package
"""
from __future__ import annotations

import uuid
import json
import logging
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
    "query_planner",
    "evidence_ranker",
    "aileak_judge",
    "drift_audit",
}


class ModelBindingMissingError(RuntimeError):
    """Raised when a required agent has no DB model binding."""


async def _resolve_model(
    agent_role: str,
    book_id: uuid.UUID,
    overrides: dict | None,
) -> tuple[str, str, str | None]:
    if overrides and overrides.get("model"):
        return (
            overrides.get("provider", "new-api"),
            overrides["model"],
            overrides.get("fallback_model"),
        )

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
    if agent_role not in PROMPTS:
        # Allow runtime system roles that reuse a nearby prompt template
        prompt_config = PROMPTS.get("query_planner") or next(iter(PROMPTS.values()))
        prompt_config = {
            **prompt_config,
            "version": f"{agent_role}-v1",
            "system_prompt": prompt_config.get("system_prompt", ""),
        }
    else:
        prompt_config = PROMPTS[agent_role]

    temperature = (overrides or {}).get("temperature", AGENT_TEMPERATURES.get(agent_role, 0.7))
    is_json = AGENT_IS_JSON.get(agent_role, False)

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

    system_prompt = prompt_config["system_prompt"]
    rendered_prompt = f"{system_prompt}\n\n{user_content}"

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

    default_manifest = assembly_manifest or {
        "entries": [],
        "excluded_entries": [],
        "budget": {
            "max_context": 128000,
            "reserved_output": 10000,
            "used": len(rendered_prompt) // 4,
        },
    }

    # Phase 1: create run only (attempt packages written after stream with real audit)
    async with async_session_factory() as db_run:
        db_run.add(run)
        await db_run.commit()

    # Phase 2: LLM — no session held
    result = await stream_with_retry(
        system_prompt=system_prompt,
        user_content=user_content,
        model=model,
        temperature=temperature,
        provider=provider,
        fallback_model=fallback_model,
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
                system_prompt=system_prompt,
                rendered_prompt=rendered_prompt,
                request_params={
                    "temperature": temperature,
                    "is_json": is_json,
                    "route_type": att.route_type,
                    "success": att.success,
                },
                assembly_manifest=default_manifest,
                l4_refs=l4_refs or [],
                l1_refs=l1_refs or [],
                l2_refs=l2_refs or [],
                l3_refs=l3_refs or [],
                genre_profile_id=genre_profile_id,
                chapter_id=chapter_id,
                scene_id=scene_id,
            )
        await db_att.commit()

    publishable, state, meta = await full_pipeline_async(
        result,
        is_json=is_json,
        agent_role=agent_role,
        book_id=book_id,
    )

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

    usage = None
    if result.prompt_tokens or result.completion_tokens:
        usage = LlmUsageEvent(
            id=uuid.uuid4(),
            book_id=book_id,
            run_id=run_id,
            provider=actual_provider,
            model_name=actual_model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            reasoning_tokens=result.reasoning_tokens,
            total_tokens=result.prompt_tokens + result.completion_tokens,
            latency_ms=result.latency_ms,
        )

    # Phase 3: single short transaction for Output + Usage + Run status + package update
    async with async_session_factory() as db_out:
        from sqlalchemy import select, update
        from app.models.tables import AgentContextPackage

        managed_run = await db_out.merge(run)
        managed_run.status = new_status
        managed_run.completed_at = completed_at
        managed_run.model_name = actual_model

        db_out.add(output)
        if usage:
            db_out.add(usage)

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
        **meta,
    }
