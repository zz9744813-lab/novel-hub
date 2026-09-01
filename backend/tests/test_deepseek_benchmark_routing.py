"""Regression coverage for DeepSeek benchmark-only request aliases."""

from unittest.mock import AsyncMock, patch

import pytest

from app.gateway.model_gateway import StreamResult
from app.model_eval.engine import _default_gateway


@pytest.mark.asyncio
async def test_deepseek_suffix_is_scoped_to_ability_evidence():
    gateway = AsyncMock(return_value=StreamResult(final_content="OK"))
    with patch(
        "app.gateway.model_gateway.stream_completion_and_collect",
        gateway,
    ):
        await _default_gateway(
            system_prompt="system",
            user_content="user",
            model="deepseek-v4-flash",
            temperature=0,
            max_tokens=128,
            provider="new-api",
        )

    assert gateway.await_args.kwargs["model"] == "deepseek-v4-flash-none"
    assert gateway.await_args.kwargs["reasoning_mode"] == "disabled"
