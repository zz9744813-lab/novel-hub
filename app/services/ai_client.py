import json
import asyncio
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Any

def _do_generate_ai_content(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str
) -> Optional[str]:
    """
    Sends a request to an OpenAI-compatible API to generate content.
    """
    if not api_key:
        print("API key is missing")
        return None

    # Standardize the base URL (strip trailing slashes, ensure /chat/completions is appended)
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
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except urllib.error.URLError as e:
        print(f"Failed to call AI API: {e}")
        if hasattr(e, 'read'):
            print(e.read().decode('utf-8'))
        return None
    except Exception as e:
        print(f"Unexpected error: {e}")
        return None

async def generate_ai_content(
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_prompt: str
) -> Optional[str]:
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(
            pool,
            _do_generate_ai_content,
            api_key,
            base_url,
            model,
            system_prompt,
            user_prompt
        )
