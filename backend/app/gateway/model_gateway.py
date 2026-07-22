"""Model Gateway - unified streaming Chat Completion, reasoning/final separation."""
import asyncio
import json
import time
import re
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator
import httpx
import os

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


REASONING_FIELDS = {"reasoning_content", "reasoning", "thinking", "thought"}


def _strip_inline_reasoning(text: str) -> tuple[str, bool]:
    """Strip inline reasoning tags from content."""
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
    max_tokens: int = 8192,
) -> StreamResult:
    """Stream and collect all chunks."""
    base_url = os.environ.get("PRIMARY_BASE_URL", "http://127.0.0.1:3000/v1")
    api_key = os.environ.get("PRIMARY_API_KEY", os.environ.get("LLM_API_KEY", "sk-test"))
    
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
    
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
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
                    
                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    
                    for field_name in REASONING_FIELDS:
                        val = delta.get(field_name)
                        if val:
                            result.reasoning_detected = True
                            result.reasoning_text += val
                    
                    content_val = delta.get("content")
                    if content_val:
                        result.final_content += content_val
                    
                    usage = data.get("usage")
                    if usage:
                        result.prompt_tokens = usage.get("prompt_tokens", 0)
                        result.completion_tokens = usage.get("completion_tokens", 0)
                        result.reasoning_tokens = usage.get("reasoning_tokens", 0)
    
    except httpx.HTTPStatusError as e:
        result.error = f"HTTP {e.response.status_code}"
        result.latency_ms = int((time.time() - start_time) * 1000)
        return result
    except Exception as e:
        result.error = str(e)
        result.latency_ms = int((time.time() - start_time) * 1000)
        return result
    
    result.final_content, inline_found = _strip_inline_reasoning(result.final_content)
    result.inline_leak_detected = inline_found
    result.latency_ms = int((time.time() - start_time) * 1000)
    
    if not result.final_content and result.reasoning_text:
        result.error = "final_content_empty"
    
    logger.info(f"Stream: reasoning={len(result.reasoning_text)}c final={len(result.final_content)}c {result.latency_ms}ms")
    return result
