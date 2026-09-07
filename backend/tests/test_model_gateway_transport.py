from __future__ import annotations

from unittest.mock import patch

import httpx
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


@pytest.mark.asyncio
@pytest.mark.parametrize("status,error,expected", [
    (400, {"type": "MissingSessionID", "message": "private payload"},
     "UPSTREAM_SESSION_REQUIRED"),
    (400, {"type": "invalid_request_error", "message": "model glm-5.3: invalid temperature"},
     "HTTP_400"),
    (404, {"code": "model_not_found"}, "MODEL_NOT_FOUND"),
    (401, {"type": "authentication_error"}, "HTTP_401"),
])
async def test_streamed_http_errors_are_read_and_classified_without_body_leaks(status, error, expected):
    import json

    async def handler(request):
        return httpx.Response(
            status, request=request,
            stream=httpx.ByteStream(json.dumps({"error": error}).encode()),
        )

    real_client = httpx.AsyncClient
    with patch("app.gateway.model_gateway.httpx.AsyncClient", side_effect=lambda **kwargs:
               real_client(transport=httpx.MockTransport(handler), **kwargs)):
        result = await stream_completion_and_collect("private prompt", "private input", "glm-5.3-flash")
    assert result.error == expected
    assert result.final_content == ""
    assert "private" not in str(result)


@pytest.mark.asyncio
async def test_relay_headers_use_own_session_and_keep_explicit_conversations_stable():
    import uuid

    requests = []

    async def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{
            "message": {"content": "OK"}, "finish_reason": "stop",
        }]})

    real_client = httpx.AsyncClient
    with patch("app.gateway.model_gateway.httpx.AsyncClient", side_effect=lambda **kwargs:
               real_client(transport=httpx.MockTransport(handler), **kwargs)):
        for provider, cid in (("new-api", None), ("new-api", None),
                              ("new-api", "our-conversation"), ("new-api", "our-conversation"),
                              ("openrouter", None)):
            result = await stream_completion_and_collect(
                "test", "test", "glm-5.3-flash", provider=provider,
                conversation_id=cid, stream=False,
            )
            assert result.error is None
        with pytest.raises(ValueError, match="invalid relay conversation id"):
            await stream_completion_and_collect(
                "test", "test", "glm-5.3-flash", provider="new-api",
                conversation_id="bad\r\nInjected: header",
            )
    assert len(requests) == 5
    first, second = [request.headers["x-opencode-session"] for request in requests[:2]]
    assert uuid.UUID(first) != uuid.UUID(second)
    assert requests[2].headers["x-opencode-session"] == requests[3].headers["x-opencode-session"]
    assert requests[2].headers["x-opencode-session"] == "our-conversation"
    assert "x-opencode-session" not in requests[4].headers
    assert all("x-opencode-client" not in request.headers for request in requests)


@pytest.mark.asyncio
async def test_gateway_retries_keep_the_same_conversation_id():
    from unittest.mock import AsyncMock
    from app.gateway.model_gateway import StreamResult, stream_with_retry

    gateway = AsyncMock(side_effect=[
        StreamResult(error="final_content_empty"), StreamResult(final_content="OK"),
        StreamResult(final_content="OK"),
    ])
    with (patch("app.gateway.model_gateway.stream_completion_and_collect", gateway),
          patch("app.gateway.model_gateway.asyncio.sleep", new_callable=AsyncMock)):
        first = await stream_with_retry("test", "test", "glm-5.3-flash", provider="new-api")
        second = await stream_with_retry("test", "test", "glm-5.3-flash", provider="new-api")
    assert first.error is None and second.error is None
    calls = gateway.await_args_list
    assert calls[0].kwargs["conversation_id"] == calls[1].kwargs["conversation_id"]
    assert calls[2].kwargs["conversation_id"] != calls[1].kwargs["conversation_id"]
