"""Model Gateway - unified streaming Chat Completion, reasoning/final separation.
Per §11 v7.3: uses ProviderAdapter for canonical event classification.
"""
import asyncio
import json
import time
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator
import httpx
import os

from app.gateway.provider_adapter import (
    CanonicalEventType, InlineReasoningParser, classify_delta, parse_stream_chunk
)

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
    has_raw_usage: bool = False


def _get_api_config():
    """Get API base URL and key, with fallback support."""
    base_url = os.environ.get("PRIMARY_BASE_URL", "")
    api_key = os.environ.get("PRIMARY_API_KEY", "")
    if not base_url or not api_key:
        base_url = os.environ.get("FALLBACK_BASE_URL", "")
        api_key = os.environ.get("FALLBACK_API_KEY", "")
    return base_url, api_key


async def stream_completion_and_collect(
    system_prompt: str,
    user_content: str,
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 8192,
) -> StreamResult:
    """Stream and collect all chunks using ProviderAdapter for classification."""
    base_url, api_key = _get_api_config()
    
    if not base_url or not api_key:
        result = StreamResult()
        result.error = "no_api_configured"
        return result

    headers = {
        "Authorization": f"Bearer {api_key}",
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

    result = StreamResult()
    start_time = time.time()
    inline_parser = InlineReasoningParser()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
            async with client.stream("POST", f"{base_url}/chat/completions",
                                      headers=headers, json=payload) as response:
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

                    # Use provider adapter to parse this chunk
                    events = parse_stream_chunk(data, inline_parser)
                    for event_type, text in events:
                        if event_type == CanonicalEventType.REASONING:
                            result.reasoning_detected = True
                            result.reasoning_text += text
                        elif event_type == CanonicalEventType.FINAL:
                            result.final_content += text
                        elif event_type == CanonicalEventType.UNKNOWN:
                            # Unknown events quarantined — don't add to final
                            result.reasoning_detected = True
                            result.reasoning_text += text

                    # Check for usage info
                    usage = data.get("usage")
                    if usage:
                        result.prompt_tokens = usage.get("prompt_tokens", 0)
                        result.completion_tokens = usage.get("completion_tokens", 0)
                        result.reasoning_tokens = usage.get("reasoning_tokens", 0)
                        result.has_raw_usage = True

        # Flush the inline parser for any remaining content
        flush_events = inline_parser.flush()
        for event_type, text in flush_events:
            if event_type == CanonicalEventType.FINAL:
                result.final_content += text
            elif event_type == CanonicalEventType.UNKNOWN:
                result.reasoning_detected = True
                result.reasoning_text += text
                result.inline_leak_detected = True

    except httpx.HTTPStatusError as e:
        result.error = f"HTTP {e.response.status_code}"
        result.latency_ms = int((time.time() - start_time) * 1000)
        return result
    except httpx.ConnectError as e:
        result.error = f"connect_error: {e}"
        result.latency_ms = int((time.time() - start_time) * 1000)
        return result
    except Exception as e:
        result.error = str(e)
        result.latency_ms = int((time.time() - start_time) * 1000)
        return result

    result.latency_ms = int((time.time() - start_time) * 1000)

    # §11.6 C-19: If final content is empty but reasoning exists, this call FAILS
    if not result.final_content and result.reasoning_text:
        result.error = "final_content_empty"

    logger.info(f"Stream: reasoning={len(result.reasoning_text)}c final={len(result.final_content)}c {result.latency_ms}ms")
    return result


async def stream_agent_call(
    system_prompt: str,
    user_content: str,
    model: str,
    temperature: float = 0.7,
    max_tokens: int = 8192,
) -> StreamResult:
    """Alias for stream_completion_and_collect — used by memory_compiler."""
    return await stream_completion_and_collect(
        system_prompt=system_prompt,
        user_content=user_content,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
