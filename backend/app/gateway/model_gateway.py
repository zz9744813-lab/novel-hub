"""Model Gateway - unified streaming Chat Completion, reasoning/final separation.
Per v7.3: C-03 streaming, C-11 retry/fallback, C-18/C-19 reasoning isolation,
§11.4 InlineReasoningParser cross-chunk state machine.
"""
import json
import time
import re
import logging
import os
from dataclasses import dataclass
import httpx

from app.gateway.provider_adapter import InlineReasoningParser, CanonicalEventType

logger = logging.getLogger("novelforge.gateway")


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


REASONING_FIELDS = {"reasoning_content", "reasoning", "thinking", "thought"}

RETRYABLE_ERRORS = {
    "final_content_empty",
    "UNTERMINATED_REASONING",
    "CONNECT_TIMEOUT",
    "READ_TIMEOUT",
    "HTTP_500",
    "HTTP_502",
    "HTTP_503",
    "HTTP_504",
    "HTTP_429",
}


def _get_provider_config(role: str = "primary", provider: str | None = None) -> dict:
    """Read provider config from environment per §2.7.

    `role` selects PRIMARY vs FALLBACK credentials.
    `provider` is the logical provider name from model bindings (openrouter/new-api/...).
    When provider-specific env vars exist they win; otherwise PRIMARY/FALLBACK is used.
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

    # Optional per-provider overrides (so UI provider field can actually route)
    if provider:
        p = provider.upper().replace("-", "_")
        base_url = os.environ.get(f"{p}_BASE_URL", base_url)
        api_key = os.environ.get(f"{p}_API_KEY", api_key)
        # Common aliases
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
        "read_timeout": 600,
        "provider": provider or role,
    }


def _strip_inline_reasoning(text: str) -> tuple[str, bool]:
    """Strip inline reasoning tags from content (fallback cleanup)."""
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
) -> StreamResult:
    """Stream and collect all chunks from a single provider attempt.

    Integrates InlineReasoningParser for cross-chunk tag handling per §11.4.
    `provider` is the logical binding provider (openrouter/new-api/...).
    `provider_role` is primary|fallback for credential selection.
    """
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

    result = StreamResult(provider_used=provider or provider_role)
    start_time = time.time()

    # §11.4: InlineReasoningParser for cross-chunk state machine
    inline_parser = InlineReasoningParser()

    try:
        timeout = httpx.Timeout(
            config["read_timeout"], connect=config["connect_timeout"]
        )
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
                        # Check for usage-only event
                        usage = data.get("usage")
                        if usage:
                            result.prompt_tokens = usage.get("prompt_tokens", 0)
                            result.completion_tokens = usage.get("completion_tokens", 0)
                            result.reasoning_tokens = usage.get("reasoning_tokens", 0)
                        continue

                    delta = choices[0].get("delta", {})

                    # C-18: Classify delta by whitelist
                    # reasoning_content / reasoning / thinking / thought -> REASONING
                    for field_name in REASONING_FIELDS:
                        val = delta.get(field_name)
                        if val:
                            result.reasoning_detected = True
                            result.reasoning_text += val

                    # content -> feed through InlineReasoningParser (§11.4)
                    content_val = delta.get("content")
                    if content_val:
                        events = inline_parser.feed(content_val)
                        for evt_type, evt_text in events:
                            if evt_type == CanonicalEventType.REASONING:
                                result.reasoning_detected = True
                                result.reasoning_text += evt_text
                                result.inline_leak_detected = True
                            elif evt_type == CanonicalEventType.FINAL:
                                result.final_content += evt_text
                            elif evt_type == CanonicalEventType.UNKNOWN:
                                # Quarantine unknown events
                                logger.debug("Unknown event quarantined")

                    # tool_calls -> TOOL (quarantine, never enters prose)
                    if delta.get("tool_calls"):
                        logger.debug("Tool call delta detected, quarantined")

                    usage = data.get("usage")
                    if usage:
                        result.prompt_tokens = usage.get("prompt_tokens", 0)
                        result.completion_tokens = usage.get("completion_tokens", 0)
                        result.reasoning_tokens = usage.get("reasoning_tokens", 0)

                # §11.4 point 4: Flush remaining carry buffer
                remaining = inline_parser.flush()
                for evt_type, evt_text in remaining:
                    if evt_type == CanonicalEventType.FINAL:
                        result.final_content += evt_text
                    elif evt_type == CanonicalEventType.UNKNOWN:
                        # §11.4 point 6: Unterminated reasoning -> block
                        result.error = "UNTERMINATED_REASONING"
                        logger.warning("Unterminated reasoning tag at stream end")

    except httpx.HTTPStatusError as e:
        result.error = f"HTTP_{e.response.status_code}"
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
        result.error = str(e)
        result.latency_ms = int((time.time() - start_time) * 1000)
        return result

    # Fallback cleanup for any remaining inline tags
    result.final_content, inline_found = _strip_inline_reasoning(result.final_content)
    if inline_found:
        result.inline_leak_detected = True

    result.latency_ms = int((time.time() - start_time) * 1000)

    # C-19: final empty + reasoning non-empty = FAILED, do NOT salvage
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
) -> StreamResult:
    """§11.11: Retry and fallback logic.
    - Same model same request: max 2 attempts (primary credentials)
    - Fallback model/provider: max 1 attempt
    - Total max 3 attempts
    - Non-retryable errors break immediately

    C-21/C-23: `provider` and `fallback_model` come from agent_model_bindings.
    """
    last_result = None
    use_model = model
    use_provider = provider

    for attempt in range(1, 4):
        # Attempts 1-2: primary model + primary credentials
        # Attempt 3: fallback model (if configured) + fallback credentials
        if attempt <= 2:
            provider_role = "primary"
            use_model = model
            use_provider = provider
        else:
            provider_role = "fallback"
            use_model = fallback_model or model
            use_provider = fallback_provider or provider

        logger.info(
            f"LLM call attempt {attempt}/3 [{provider_role}] "
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
        )
        result.attempt = attempt
        result.provider_used = use_provider or provider_role

        # Success: got final content with no error
        if result.final_content and not result.error:
            return result

        last_result = result

        # Check if error is retryable per §11.11
        if result.error and result.error not in RETRYABLE_ERRORS:
            logger.warning(f"Non-retryable error: {result.error}, stopping")
            break

        # If no fallback model configured, don't bother with attempt 3 model switch noise
        if attempt == 2 and not fallback_model:
            logger.warning(
                f"Attempt {attempt} failed: {result.error}, no fallback_model configured"
            )
            # still allow a third try on same model with fallback credentials
        else:
            logger.warning(
                f"Attempt {attempt} failed: {result.error}, "
                f"{'will retry' if attempt < 3 else 'all attempts exhausted'}"
            )

    return last_result or StreamResult(error="all_attempts_failed")


async def stream_agent_call(
    system_prompt: str,
    user_content: str,
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 16384,
    provider: str | None = None,
    fallback_model: str | None = None,
) -> StreamResult:
    """Compatibility wrapper for memory_compiler.py and other callers.
    Routes through stream_with_retry for C-11 compliance.
    """
    return await stream_with_retry(
        system_prompt=system_prompt,
        user_content=user_content,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        provider=provider,
        fallback_model=fallback_model,
    )
