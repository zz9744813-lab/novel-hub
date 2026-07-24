"""Unified Agent caller - handles streaming, normalization, leak guard, and 5-level output storage.
Per §10 + §11 v7.3.

Key fixes:
- Uses stream_with_retry for C-11 compliance
- Saves raw_provider_response summary
- §2.5: Uses short-lived sessions — caller opens/closes its own sessions,
  never holds a session across the LLM await
"""
import uuid
import json
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import async_session_factory
from app.gateway.model_gateway import stream_with_retry, StreamResult
from app.gateway.publish_pipeline import full_pipeline, PublishState
from app.prompts import PROMPTS, AGENT_MODELS, AGENT_TEMPERATURES, AGENT_IS_JSON
from app.models import AgentRun, AgentRunOutput, LlmUsageEvent

logger = logging.getLogger("novelforge.agents")


async def call_agent(
    db: AsyncSession,
    book_id: uuid.UUID,
    agent_role: str,
    user_content: str,
    chapter_id: uuid.UUID | None = None,
    scene_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
    overrides: dict | None = None,
) -> tuple[AgentRun, str | dict | None, dict]:
    """Call an agent, store outputs, return (run, publishable, metadata).

    §11.11: Uses stream_with_retry for automatic retry/fallback.
    §11.6: Stores all 5 levels of output.
    §2.5: LLM call happens BETWEEN short-lived sessions, not inside one.
    """
    prompt_config = PROMPTS[agent_role]
    model = (overrides or {}).get("model", AGENT_MODELS.get(agent_role, "deepseek-v4-flash"))
    temperature = (overrides or {}).get("temperature", AGENT_TEMPERATURES.get(agent_role, 0.7))
    is_json = AGENT_IS_JSON.get(agent_role, False)

    # §2.5 Phase 1: Create AgentRun in a short-lived session
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
    async with async_session_factory() as db_run:
        db_run.add(run)
        await db_run.commit()

    # §2.5 Phase 2: LLM call — NO session held
    result = await stream_with_retry(
        system_prompt=prompt_config["system_prompt"],
        user_content=user_content,
        model=model,
        temperature=temperature,
    )

    # Process through publish pipeline (normalization + leak guard)
    publishable, state, meta = full_pipeline(result, is_json=is_json)

    # §11.6: Store all 5 levels of output
    raw_response_summary = {
        "provider": result.provider_used,
        "model": model,
        "attempt": result.attempt,
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
        provider=result.provider_used,
        model_name=model,
        raw_provider_response=raw_response_summary,
        reasoning_text=result.reasoning_text[:10000] if result.reasoning_text else None,
        final_content=result.final_content[:50000] if result.final_content else None,
        normalized_content=json.dumps(publishable, ensure_ascii=False) if isinstance(publishable, dict) else publishable,
        publishable_content=json.dumps(publishable, ensure_ascii=False) if isinstance(publishable, dict) else publishable,
        reasoning_detected=result.reasoning_detected,
        inline_leak_detected=result.inline_leak_detected,
        leak_status="checked" if state != PublishState.BLOCKED else "blocked",
        output_integrity=state.value if publishable else "blocked",
    )

    # §2.5 Phase 3: Save outputs in a short-lived session
    usage = None
    if result.prompt_tokens or result.completion_tokens:
        usage = LlmUsageEvent(
            id=uuid.uuid4(),
            book_id=book_id,
            run_id=run_id,
            provider=result.provider_used,
            model_name=model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            reasoning_tokens=result.reasoning_tokens,
            total_tokens=result.prompt_tokens + result.completion_tokens,
            latency_ms=result.latency_ms,
        )

    new_status = "completed" if publishable is not None else "failed"
    completed_at = datetime.now(timezone.utc)

    async with async_session_factory() as db_out:
        db_out.add(output)
        if usage:
            db_out.add(usage)
        # Update run status via merge (run is detached from db_run)
        run.status = new_status
        run.completed_at = completed_at
        db_out.merge(run)
        await db_out.commit()

    return run, publishable, {
        "reasoning_detected": result.reasoning_detected,
        "inline_leak_detected": result.inline_leak_detected,
        "error": result.error,
        "latency_ms": result.latency_ms,
        "attempt": result.attempt,
        "provider_used": result.provider_used,
        "block_reason": meta.get("block_reason"),
        **meta,
    }
