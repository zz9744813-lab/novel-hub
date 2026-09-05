from __future__ import annotations

from unittest.mock import patch

import pytest

from app.gateway.model_gateway import stream_completion_and_collect


@pytest.mark.asyncio
@pytest.mark.parametrize("damaged", [False, True])
async def test_nonstream_completion_preserves_text_and_rejects_decode_damage(damaged):
    captured: dict = {}
    content = '{"姜遥":{"can":["检查水痕"]}}'
    if damaged:
        content = content.replace("姜", "\ufffd姜")

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aread(self):
            return b"{}"

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": content,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8},
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, *, headers, json):
            captured.update(method=method, url=url, headers=headers, payload=json)
            return Response()

    with patch("app.gateway.model_gateway.httpx.AsyncClient", return_value=Client()):
        result = await stream_completion_and_collect(
            system_prompt="system",
            user_content="user",
            model="glm-5.2",
            provider="new-api",
            reasoning_mode="disabled",
            stream=False,
        )

    assert captured["payload"]["stream"] is False
    assert result.error == ("INVALID_RESPONSE_ENCODING" if damaged else None)
    assert result.final_content == content
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 8
