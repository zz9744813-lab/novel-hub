"""Model Gateway - unified streaming Chat Completion, reasoning/final separation.
Per v7.3: C-03 streaming, C-11 retry/fallback, C-18/C-19 reasoning isolation,
§11.4 InlineReasoningParser cross-chunk state machine.

P0-05: AttemptRecord on every real request; StreamResult carries full attempt audit.
"""
from __future__ import annotations

import json
import time
import re
import logging
import os
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import httpx

from app.gateway.provider_adapter import InlineReasoningParser, CanonicalEventType

logger = logging.getLogger("novelforge.gateway")


@dataclass
class AttemptRecord:
    attempt_no: int
    provider: str
    model: str
    route_type: str  # primary | retry | fallback
    started_at: datetime
    completed_at: datetime
    latency_ms: int
    success: bool
    error_code: str | None
    first_token_ms: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class StreamResult:
    reasoning_text: str = ""
    final_content: str = ""
    reasoning_detected: bool = False
    inline_leak_detected: bool = False
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: int = 0
    attempt: int = 0
    provider_used: str = "primary"
    actual_provider: str = ""
    actual_model: str = ""
    successful_attempt_no: int | None = None
    first_token_ms: int | None = None  # v9.6: measured TTFT, never latency/2
    attempts: list[AttemptRecord] = field(default_factory=list)


REASONING_FIELDS = {"reasoning_content", "reasoning", "thinking", "thought"}

RETRYABLE_ERRORS = {
    "final_content_empty",
    "UNTERMINATED_REASONING",
    "CONNECT_TIMEOUT",
    "READ_TIMEOUT",
    "HTTP_404",
    "HTTP_500",
    "HTTP_502",
    "HTTP_503",
    "HTTP_504",
    "HTTP_429",
    "MODEL_NOT_FOUND",
}


def _get_provider_config(role: str = "primary", provider: str | None = None) -> dict:
    """Read provider config from environment per §2.7.

    `role` selects PRIMARY vs FALLBACK credentials.
    `provider` is the logical provider name from model bindings.
    """
    prefix = "PRIMARY" if role == "primary" else "FALLBACK"
    base_url = os.environ.get(
        f"{prefix}_BASE_URL",
        os.environ.get("PRIMARY_BASE_URL", "http://127.0.0.1:3000/v1"),
    )
    api_key = os.environ.get(
        f"{prefix}_API_KEY",
        os.environ.get("PRIMARY_API_KEY", os.environ.get("LLM_API_KEY", "sk-test")),
    )

    if provider:
        p = provider.upper().replace("-", "_")
        base_url = os.environ.get(f"{p}_BASE_URL", base_url)
        api_key = os.environ.get(f"{p}_API_KEY", api_key)
        if provider.lower() in ("new-api", "new_api", "newapi"):
            base_url = os.environ.get("NEW_API_BASE_URL", os.environ.get("PRIMARY_BASE_URL", base_url))
            api_key = os.environ.get("NEW_API_API_KEY", os.environ.get("PRIMARY_API_KEY", api_key))
        elif provider.lower() == "openrouter":
            base_url = os.environ.get("OPENROUTER_BASE_URL", base_url)
            api_key = os.environ.get("OPENROUTER_API_KEY", api_key)

    return {
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "connect_timeout": 15,
        "read_timeout": int(os.environ.get("LLM_READ_TIMEOUT", "600")),
        "provider": provider or role,
    }


def _strip_inline_reasoning(text: str) -> tuple[str, bool]:
    found = False
    if re.search(r"<reasoning>.*?</reasoning>", text, re.DOTALL):
        found = True
        text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.DOTALL)
    if re.search(r"<thinking>.*?</thinking>", text, re.DOTALL):
        found = True
        text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
    return text.strip(), found


async def stream_completion_and_collect(
    system_prompt: str,
    user_content: str,
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 16384,
    provider_role: str = "primary",
    provider: str | None = None,
    response_format: dict | None = None,
) -> StreamResult:
    """Stream and collect all chunks from a single provider attempt."""
    config = _get_provider_config(provider_role, provider=provider)

    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if response_format:
        payload["response_format"] = response_format

    result = StreamResult(
        provider_used=provider or provider_role,
        actual_provider=provider or provider_role,
        actual_model=model,
    )
    start_time = time.time()
    inline_parser = InlineReasoningParser()

    try:
        timeout = httpx.Timeout(config["read_timeout"], connect=config["connect_timeout"])
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{config['base_url']}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    if line.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    choices = data.get("choices", [])
                    if not choices:
                        usage = data.get("usage")
                        if usage:
                            result.prompt_tokens = usage.get("prompt_tokens", 0)
                            result.completion_tokens = usage.get("completion_tokens", 0)
                            result.reasoning_tokens = usage.get("reasoning_tokens", 0)
                        continue

                    delta = choices[0].get("delta", {})

                    for field_name in REASONING_FIELDS:
                        val = delta.get(field_name)
                        if val:
                            if result.first_token_ms is None:
                                result.first_token_ms = int((time.time() - start_time) * 1000)
                            result.reasoning_detected = True
                            result.reasoning_text += val

                    content_val = delta.get("content")
                    if content_val:
                        if result.first_token_ms is None:
                            result.first_token_ms = int((time.time() - start_time) * 1000)
                        events = inline_parser.feed(content_val)
                        for evt_type, evt_text in events:
                            if evt_type == CanonicalEventType.REASONING:
                                result.reasoning_detected = True
                                result.reasoning_text += evt_text
                                result.inline_leak_detected = True
                            elif evt_type == CanonicalEventType.FINAL:
                                result.final_content += evt_text
                            elif evt_type == CanonicalEventType.UNKNOWN:
                                logger.debug("Unknown event quarantined")

                    if delta.get("tool_calls"):
                        logger.debug("Tool call delta detected, quarantined")

                    usage = data.get("usage")
                    if usage:
                        result.prompt_tokens = usage.get("prompt_tokens", 0)
                        result.completion_tokens = usage.get("completion_tokens", 0)
                        result.reasoning_tokens = usage.get("reasoning_tokens", 0)

                remaining = inline_parser.flush()
                for evt_type, evt_text in remaining:
                    if evt_type == CanonicalEventType.FINAL:
                        result.final_content += evt_text
                    elif evt_type == CanonicalEventType.UNKNOWN:
                        result.error = "UNTERMINATED_REASONING"
                        logger.warning("Unterminated reasoning tag at stream end")

    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        result.error = f"HTTP_{code}"
        # Normalize common "model not found" bodies so attempt audit is clearer
        try:
            body = e.response.text[:500].lower()
            if code in (400, 404) and ("model" in body or "not found" in body or "invalid" in body):
                result.error = "MODEL_NOT_FOUND" if "model" in body else result.error
        except Exception:
            pass
        result.latency_ms = int((time.time() - start_time) * 1000)
        return result
    except httpx.ConnectTimeout:
        result.error = "CONNECT_TIMEOUT"
        result.latency_ms = int((time.time() - start_time) * 1000)
        return result
    except httpx.ReadTimeout:
        result.error = "READ_TIMEOUT"
        result.latency_ms = int((time.time() - start_time) * 1000)
        return result
    except Exception as e:
        msg = str(e)
        # httpx may raise without HTTPStatusError in some adapters
        if "404" in msg and "model" in msg.lower():
            result.error = "MODEL_NOT_FOUND"
        else:
            result.error = msg
        result.latency_ms = int((time.time() - start_time) * 1000)
        return result

    result.final_content, inline_found = _strip_inline_reasoning(result.final_content)
    if inline_found:
        result.inline_leak_detected = True

    result.latency_ms = int((time.time() - start_time) * 1000)

    if not result.final_content and result.reasoning_text:
        result.error = "final_content_empty"
        logger.warning(
            f"REASONING_ONLY_RESPONSE blocked: "
            f"reasoning={len(result.reasoning_text)}c final=0c"
        )

    logger.info(
        f"Stream [{provider_role}] reasoning={len(result.reasoning_text)}c "
        f"final={len(result.final_content)}c {result.latency_ms}ms"
    )
    return result


async def stream_with_retry(
    system_prompt: str,
    user_content: str,
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 16384,
    provider: str | None = None,
    fallback_model: str | None = None,
    fallback_provider: str | None = None,
    fallbacks: list[dict] | None = None,
    response_format: dict | None = None,
) -> StreamResult:
    """§11.11 + P0-05 + v9.5 §49–§51: fallback list with full AttemptRecord audit.

    Default attempt order (spec §50):
      Attempt 1 Primary → Attempt 2 Primary Retry → Attempt 3 Fallback 1 → Attempt 4 Fallback 2
    `fallbacks` is a list of {"model", "provider"} targets; the legacy
    `fallback_model`/`fallback_provider` args map onto that list for compat.
    """
    if fallbacks is None:
        fallbacks = (
            [{"model": fallback_model, "provider": fallback_provider}]
            if fallback_model else []
        )

    attempts: list[AttemptRecord] = []
    last_result: StreamResult | None = None

    # route plan: [primary, primary-retry, *fallbacks] capped at 4 total
    route: list[tuple[str, str, str]] = [
        (model, provider, "primary"),
        (model, provider, "retry"),
    ]
    for fb in fallbacks[:2]:
        route.append((fb.get("model") or model, fb.get("provider") or provider, "fallback"))

    for attempt in range(1, len(route) + 1):
        use_model, use_provider, route_type = route[attempt - 1]
        provider_role = "primary" if route_type == "primary" else ("retry" if route_type == "retry" else "fallback")

        started = datetime.now(timezone.utc)
        logger.info(
            f"LLM call attempt {attempt}/{len(route)} [{route_type}] "
            f"provider={use_provider} model={use_model}"
        )

        result = await stream_completion_and_collect(
            system_prompt=system_prompt,
            user_content=user_content,
            model=use_model,
            temperature=temperature,
            max_tokens=max_tokens,
            provider_role=provider_role,
            provider=use_provider,
            response_format=response_format,
        )
        completed = datetime.now(timezone.utc)
        success = bool(result.final_content and not result.error)

        rec = AttemptRecord(
            attempt_no=attempt,
            provider=use_provider or provider_role,
            model=use_model,
            route_type=route_type,
            started_at=started,
            completed_at=completed,
            latency_ms=result.latency_ms,
            success=success,
            error_code=result.error,
            first_token_ms=result.first_token_ms,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )
        attempts.append(rec)

        result.attempt = attempt
        result.provider_used = use_provider or provider_role
        result.actual_provider = use_provider or provider_role
        result.actual_model = use_model
        result.attempts = list(attempts)

        if success:
            result.successful_attempt_no = attempt
            return result

        last_result = result

        if result.error and result.error not in RETRYABLE_ERRORS:
            logger.warning(f"Non-retryable error: {result.error}, stopping")
            break

        # rate-limit / transient: backoff before next attempt
        if result.error in {"HTTP_429", "HTTP_503", "HTTP_502", "HTTP_504", "READ_TIMEOUT"}:
            delay = min(45.0, 4.0 * (2 ** (attempt - 1)))
            logger.warning("Backoff %.1fs after %s (attempt %s)", delay, result.error, attempt)
            await asyncio.sleep(delay)

        if attempt == 2 and not fallbacks:
            logger.warning(
                f"Attempt {attempt} failed: {result.error}, no fallback configured"
            )
        else:
            logger.warning(
                f"Attempt {attempt} failed: {result.error}, "
                f"{'will retry' if attempt < len(route) else 'all attempts exhausted'}"
            )

    out = last_result or StreamResult(error="all_attempts_failed")
    out.attempts = attempts
    if attempts:
        last = attempts[-1]
        out.actual_provider = last.provider
        out.actual_model = last.model
        out.provider_used = last.provider
        out.attempt = last.attempt_no
    return out


async def stream_agent_call(
    system_prompt: str,
    user_content: str,
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 16384,
    provider: str | None = None,
    fallback_model: str | None = None,
    fallbacks: list[dict] | None = None,
) -> StreamResult:
    return await stream_with_retry(
        system_prompt=system_prompt,
        user_content=user_content,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        provider=provider,
        fallback_model=fallback_model,
        fallbacks=fallbacks,
    )
