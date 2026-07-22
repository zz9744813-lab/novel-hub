import json
import asyncio
import httpx
from typing import Optional, AsyncGenerator

async def generate_ai_content_stream(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str
) -> AsyncGenerator[str, None]:
    """
    Sends a request to an OpenAI-compatible API to generate content with streaming.
    """
    if not api_key:
        yield "[Error: API key is missing]"
        return

    base_url = base_url.rstrip("/")
    if not base_url.endswith("/chat/completions"):
        url = f"{base_url}/chat/completions"
    else:
        url = base_url

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "stream": True
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", url, headers=headers, json=data) as response:
                if response.status_code != 200:
                    yield f"[Error: API returned {response.status_code}]"
                    return

                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    if line == "[DONE]":
                        break
                    
                    try:
                        chunk = json.loads(line)
                        content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        yield f"[Error: {str(e)}]"

async def generate_ai_content(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str
) -> Optional[str]:
    """
    Non-streaming version for backward compatibility or simple tasks.
    """
    full_content = ""
    async for chunk in generate_ai_content_stream(api_key, base_url, model, system_prompt, user_prompt):
        if chunk.startswith("[Error:"):
            return None
        full_content += chunk
    return full_content
