"""Unified Agent caller - handles streaming, normalization, leak guard, and 5-level output storage."""
import uuid
import json
import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from app.gateway.model_gateway import stream_completion_and_collect, StreamResult
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
    """Call an agent, store outputs, return (run, publishable, metadata)."""
    prompt_config = PROMPTS[agent_role]
    model = (overrides or {}).get("model", AGENT_MODELS.get(agent_role, "deepseek-v4-flash"))
    temperature = (overrides or {}).get("temperature", AGENT_TEMPERATURES.get(agent_role, 0.7))
    is_json = AGENT_IS_JSON.get(agent_role, False)

    run = AgentRun(
        id=uuid.uuid4(),
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
    db.add(run)
    await db.flush()

    result = await stream_completion_and_collect(
        system_prompt=prompt_config["system_prompt"],
        user_content=user_content,
        model=model,
        temperature=temperature,
    )

    publishable, state, meta = full_pipeline(result, is_json=is_json)

    output = AgentRunOutput(
        run_id=run.id,
        book_id=book_id,
        agent_role=agent_role,
        provider="primary",
        model_name=model,
        reasoning_text=result.reasoning_text,
        final_content=result.final_content,
        normalized_content=json.dumps(publishable, ensure_ascii=False) if isinstance(publishable, dict) else publishable,
        publishable_content=json.dumps(publishable, ensure_ascii=False) if isinstance(publishable, dict) else publishable,
        reasoning_detected=result.reasoning_detected,
        inline_leak_detected=result.inline_leak_detected,
        leak_status="checked" if state != PublishState.BLOCKED else "blocked",
        output_integrity=state.value if publishable else "blocked",
    )
    db.add(output)

    if result.prompt_tokens or result.completion_tokens:
        usage = LlmUsageEvent(
            id=uuid.uuid4(),
            book_id=book_id,
            run_id=run.id,
            provider="primary",
            model_name=model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            reasoning_tokens=result.reasoning_tokens,
            total_tokens=result.prompt_tokens + result.completion_tokens,
        )
        db.add(usage)

    if publishable is not None:
        run.status = "completed"
    else:
        run.status = "failed"
    run.completed_at = datetime.now(timezone.utc)
    await db.flush()

    return run, publishable, {
        "reasoning_detected": result.reasoning_detected,
        "inline_leak_detected": result.inline_leak_detected,
        "error": result.error,
        "latency_ms": result.latency_ms,
        "block_reason": meta.get("block_reason"),
        **meta,
    }
