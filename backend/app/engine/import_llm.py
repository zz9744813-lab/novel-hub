"""Import LLM helper — stream gateway only, one schema repair, no Book pre-create."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, Type

from pydantic import BaseModel

from app.contracts.import_contracts import IMPORT_CONTRACTS
from app.gateway.model_gateway import stream_with_retry

logger = logging.getLogger("novelforge.import_llm")

# Reuse production model binding for outline_parser when import roles unbound
DEFAULT_IMPORT_MODEL = "deepseek-v4-flash"
DEFAULT_IMPORT_PROVIDER = "new-api"

# process-local throttle to reduce 429 storms across sequential import agents
_last_call_ts = 0.0
_MIN_GAP_SEC = 1.2


async def resolve_import_model() -> tuple[str, str, str | None]:
    try:
        from app.database import async_session_factory
        from app.v74_utils import ModelBindingService

        async with async_session_factory() as db:
            svc = ModelBindingService(db)
            # global-ish: pick first book binding for outline_parser
            binding = await svc.get_binding("outline_parser", uuid.UUID(int=0))
            if binding is None:
                # try any binding row
                from sqlalchemy import select
                from app.models.tables import AgentModelBinding

                row = (
                    await db.execute(
                        select(AgentModelBinding)
                        .where(AgentModelBinding.agent_role == "outline_parser")
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if row:
                    return row.provider, row.primary_model, row.fallback_model
            else:
                return binding.provider, binding.primary_model, binding.fallback_model
    except Exception as e:
        logger.warning("import model resolve fallback: %s", e)
    return DEFAULT_IMPORT_PROVIDER, DEFAULT_IMPORT_MODEL, None


def _extract_json(text: str) -> Any:
    if not text:
        raise ValueError("empty_llm_output")
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", t)
        if not m:
            raise
        return json.loads(m.group(0))


def validate_import(role: str, payload: Any) -> tuple[dict | None, str | None]:
    model: Type[BaseModel] | None = IMPORT_CONTRACTS.get(role)
    if model is None:
        return payload if isinstance(payload, dict) else None, "unknown_role"
    try:
        obj = model.model_validate(payload)
        return obj.model_dump(mode="json"), None
    except Exception as e:
        return None, str(e)


async def _throttle() -> None:
    global _last_call_ts
    now = time.monotonic()
    wait = _MIN_GAP_SEC - (now - _last_call_ts)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_call_ts = time.monotonic()


async def call_import_agent(
    *,
    role: str,
    system_prompt: str,
    user_content: str,
    temperature: float = 0.1,
) -> tuple[dict | None, dict]:
    """Returns (validated_dict|None, meta). Uses streaming gateway only."""
    provider, model, fallback = await resolve_import_model()
    schema_model = IMPORT_CONTRACTS.get(role)
    response_format = None
    if schema_model is not None:
        schema = schema_model.model_json_schema()
        # soft schema for gateway
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": f"import_{role}"[:64],
                "strict": False,
                "schema": schema,
            },
        }

    meta: dict[str, Any] = {
        "role": role,
        "provider": provider,
        "model": model,
        "attempts": 0,
    }

    async def _once(extra_user: str = "") -> tuple[dict | None, str | None, Any]:
        await _throttle()
        result = await stream_with_retry(
            system_prompt=system_prompt,
            user_content=user_content + extra_user,
            model=model,
            temperature=temperature,
            provider=provider,
            fallback_model=fallback,
            response_format=response_format,
            max_tokens=8192,
        )
        meta["attempts"] = meta.get("attempts", 0) + 1
        meta["latency_ms"] = getattr(result, "latency_ms", None)
        meta["error"] = getattr(result, "error", None)
        raw = getattr(result, "final_content", None) or getattr(result, "content", None) or ""
        if result.error and not raw:
            return None, f"llm_error:{result.error}", raw
        try:
            parsed = _extract_json(raw if isinstance(raw, str) else json.dumps(raw))
        except Exception as e:
            return None, f"json_parse:{e}", raw
        validated, verr = validate_import(role, parsed)
        return validated, verr, raw

    validated, err, raw = await _once()
    if validated is not None:
        meta["ok"] = True
        return validated, meta

    # one repair — extra throttle already in _once
    repair = (
        "\n\n【修复】上一次输出不符合 JSON Schema，错误："
        f"{err}\n请只输出合法 JSON 对象，不要 markdown。"
    )
    # if rate-limited, wait a bit more before repair
    if err and "429" in str(err):
        await asyncio.sleep(8)
    validated2, err2, raw2 = await _once(repair)
    if validated2 is not None:
        meta["ok"] = True
        meta["repaired"] = True
        return validated2, meta

    meta["ok"] = False
    meta["validation_error"] = err2 or err
    meta["raw_preview"] = (str(raw2 or raw) or "")[:800]
    return None, meta
