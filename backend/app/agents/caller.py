"""Unified Agent caller - v7.4: model bindings + route events + context packages.

C-21: Read model from agent_model_bindings (DB), not .env at runtime
C-22: Record model_route_events per attempt
C-23: Missing binding fails closed (no silent default for production agents)
C-35: Persist agent_context_packages per attempt
§2.5: Short-lived sessions — never hold DB across LLM await
"""
from __future__ import annotations

import uuid
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

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

# Roles that MUST have an explicit binding (C-21/C-23). No silent env default.
STRICT_ROLES = {
    "draft_writer",
    "chapter_planner",
    "review_agent",
    "state_extractor",
    "local_rewrite_editor",
    "outline_parser",
}


class ModelBindingMissingError(RuntimeError):
    """Raised when a required agent has no DB model binding."""


async def _resolve_model(
    agent_role: str,
    book_id: uuid.UUID,
    overrides: dict | None,
) -> tuple[str, str, str | None]:
    """C-21: Resolve model from DB binding.

    Priority: override > book binding > global binding.
    Strict roles: missing binding raises (no silent default).
    Returns (provider, primary_model, fallback_model)
    """
    if overrides and overrides.get("model"):
        return (
            overrides.get("provider", "openrouter"),
            overrides["model"],
            overrides.get("fallback_model"),
        )

    async with async_session_factory() as db:
        svc = ModelBindingService(db)
        binding = await svc.get_binding(agent_role, book_id)
        if binding:
            return binding.provider, binding.primary_model, binding.fallback_model

    if agent_role in STRICT_ROLES:
        raise ModelBindingMissingError(
            f"No model binding for agent_role={agent_role} book_id={book_id}. "
            "Configure it in the Model Binding panel before running production agents."
        )

    # Non-strict utility roles only: still fail closed rather than invent a model
    logger.error(f"No DB binding for {agent_role}; refusing silent default")
    raise ModelBindingMissingError(
        f"No model binding for agent_role={agent_role}"
    )


async def call_agent(
    db: AsyncSession,
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
    """Call an agent, store outputs, return (run, publishable, metadata).

    v7.4 flow:
    1. Resolve model binding from DB (strict)
    2. Create AgentRun
    3. Save Context Package (attempt 1)
    4. Call LLM with provider (no session held)
    5. Record model_route_event
    6. Async publish pipeline (incl. Layer-2 AILeakJudge for prose)
    7. Save outputs + update context package publish_state
    """
    prompt_config = PROMPTS[agent_role]
    temperature = (overrides or {}).get("temperature", AGENT_TEMPERATURES.get(agent_role, 0.7))
    is_json = AGENT_IS_JSON.get(agent_role, False)

    # C-21/C-23: Resolve from DB binding — fail closed if missing
    try:
        provider, model, fallback_model = await _resolve_model(agent_role, book_id, overrides)
    except ModelBindingMissingError as e:
        logger.error(str(e))
        # Create a failed run record so the UI can see the misconfiguration
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
    attempt_no = 1

    # Phase 1: Create AgentRun + Context Package + Route Event in short session
    run_id = uuid.uuid4()
    ctx_pkg_id: uuid.UUID | None = None
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

    async with async_session_factory() as db_run:
        db_run.add(run)

        # C-35: Context Package
        pkg = await save_context_package(
            db=db_run,
            run_id=run_id,
            attempt_no=attempt_no,
            book_id=book_id,
            agent_role=agent_role,
            provider=provider,
            model=model,
            prompt_version=prompt_config["version"],
            system_prompt=system_prompt,
            rendered_prompt=rendered_prompt,
            request_params={"temperature": temperature, "is_json": is_json},
            assembly_manifest=default_manifest,
            l4_refs=l4_refs or [],
            l1_refs=l1_refs or [],
            l2_refs=l2_refs or [],
            l3_refs=l3_refs or [],
            genre_profile_id=genre_profile_id,
            chapter_id=chapter_id,
            scene_id=scene_id,
        )

        # C-22: Primary route event
        await record_model_route(
            db=db_run,
            run_id=run_id,
            attempt_no=attempt_no,
            agent_role=agent_role,
            configured_provider=provider,
            configured_model=model,
            actual_provider=provider,
            actual_model=model,
            route_type="primary",
            reason=None,
        )
        await db_run.commit()
        ctx_pkg_id = pkg.id

    # Phase 2: LLM call — NO session held; pass provider from binding
    result = await stream_with_retry(
        system_prompt=system_prompt,
        user_content=user_content,
        model=model,
        temperature=temperature,
        provider=provider,
        fallback_model=fallback_model,
    )

    actual_provider = result.provider_used or provider
    actual_model = model
    route_type = "primary"

    # If primary exhausted and binding has fallback_model, ensure route event is logged
    # (stream_with_retry already tried fallback_model on attempt 3)
    if result.error and fallback_model and result.provider_used and result.provider_used != provider:
        route_type = "fallback"
        actual_model = fallback_model
        attempt_no = max(result.attempt, 2)

        async with async_session_factory() as db_fb:
            await record_model_route(
                db=db_fb,
                run_id=run_id,
                attempt_no=attempt_no,
                agent_role=agent_role,
                configured_provider=provider,
                configured_model=model,
                actual_provider=actual_provider,
                actual_model=actual_model,
                route_type="fallback",
                reason=result.error,
            )
            await save_context_package(
                db=db_fb,
                run_id=run_id,
                attempt_no=attempt_no,
                book_id=book_id,
                agent_role=agent_role,
                provider=actual_provider,
                model=actual_model,
                prompt_version=prompt_config["version"],
                system_prompt=system_prompt,
                rendered_prompt=rendered_prompt,
                request_params={
                    "temperature": temperature,
                    "is_json": is_json,
                    "fallback": True,
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
            await db_fb.commit()

    # Process through async publish pipeline (Layer-2 for prose)
    publishable, state, meta = await full_pipeline_async(
        result,
        is_json=is_json,
        agent_role=agent_role,
        book_id=book_id,
    )

    # Phase 3: Save outputs + update context package
    raw_response_summary = {
        "provider": actual_provider,
        "model": actual_model,
        "attempt": attempt_no,
        "route_type": route_type,
        "error": result.error,
        "latency_ms": result.latency_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "reasoning_tokens": result.reasoning_tokens,
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

    new_status = "completed" if publishable is not None else "failed"
    completed_at = datetime.now(timezone.utc)
    publish_state = state.value if publishable else "blocked"
    block_reason = meta.get("block_reason")

    async with async_session_factory() as db_out:
        db_out.add(output)
        if usage:
            db_out.add(usage)
        run.status = new_status
        run.completed_at = completed_at
        run.model_name = actual_model
        db_out.merge(run)

        from app.models.tables import AgentContextPackage
        from sqlalchemy import update

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
            )
        )
        await db_out.commit()

    return run, publishable, {
        "reasoning_detected": result.reasoning_detected,
        "inline_leak_detected": result.inline_leak_detected,
        "error": result.error,
        "latency_ms": result.latency_ms,
        "attempt": attempt_no,
        "provider_used": actual_provider,
        "model_used": actual_model,
        "route_type": route_type,
        "block_reason": block_reason,
        "context_package_id": str(ctx_pkg_id) if ctx_pkg_id else None,
        **meta,
    }
