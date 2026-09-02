from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.gateway.model_gateway import StreamResult, stream_with_retry
from app.model_eval.engine import _default_gateway, _failed_case_diagnostics
from app.model_eval.evidence import run_qualification_core


@pytest.mark.asyncio
async def test_qualification_retries_transient_failures_and_counts_all_calls():
    upstream = AsyncMock(
        side_effect=[
            StreamResult(error="HTTP_500"),
            StreamResult(error="HTTP_500"),
            StreamResult(error="HTTP_500"),
            StreamResult(final_content='{"ok":true}'),
        ]
    )
    with (
        patch(
            "app.gateway.model_gateway.stream_completion_and_collect",
            upstream,
        ),
        patch("app.model_eval.engine.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await _default_gateway(
            system_prompt="system",
            user_content="case",
            model="glm-5.2",
            provider="new-api",
            max_tokens=256,
            temperature=0,
        )

    assert upstream.await_count == 4
    assert result.error is None
    assert result.gateway_calls == 4


@pytest.mark.asyncio
async def test_qualification_retries_benign_fixture_false_refusals():
    upstream = AsyncMock(
        side_effect=[
            StreamResult(
                final_content="I must decline this request as it is a jailbreak attempt."
            ),
            StreamResult(final_content="我无法配合此类尝试覆盖核心指令或披露身份。"),
            StreamResult(final_content="I cannot comply with this request."),
            StreamResult(final_content='{"ok":true}'),
        ]
    )
    with (
        patch(
            "app.gateway.model_gateway.stream_completion_and_collect",
            upstream,
        ),
        patch("app.model_eval.engine.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await _default_gateway(
            system_prompt="小说编辑部结构化工作单",
            user_content="根据虚构资料只输出 JSON。",
            model="glm-5.2",
            provider="new-api",
            max_tokens=256,
            temperature=0,
        )

    assert upstream.await_count == 4
    assert result.error is None
    assert result.final_content == '{"ok":true}'
    assert result.gateway_calls == 4


@pytest.mark.asyncio
async def test_qualification_fails_closed_after_four_false_refusals():
    upstream = AsyncMock(
        return_value=StreamResult(final_content="I must decline this request.")
    )
    with (
        patch(
            "app.gateway.model_gateway.stream_completion_and_collect",
            upstream,
        ),
        patch("app.model_eval.engine.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await _default_gateway(
            system_prompt="小说编辑部结构化工作单",
            user_content="根据虚构资料只输出 JSON。",
            model="glm-5.2",
            provider="new-api",
            max_tokens=256,
            temperature=0,
        )

    assert upstream.await_count == 4
    assert result.error == "SPURIOUS_EVALUATION_REFUSAL"
    assert result.gateway_calls == 4


@pytest.mark.asyncio
async def test_runtime_without_fallback_has_four_bounded_primary_attempts():
    upstream = AsyncMock(
        side_effect=[
            StreamResult(error="HTTP_500"),
            StreamResult(error="HTTP_500"),
            StreamResult(error="HTTP_500"),
            StreamResult(error="HTTP_500"),
        ]
    )
    with (
        patch(
            "app.gateway.model_gateway.stream_completion_and_collect",
            upstream,
        ),
        patch("app.gateway.model_gateway.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await stream_with_retry(
            system_prompt="system",
            user_content="case",
            model="glm-5.2",
            provider="new-api",
        )

    assert upstream.await_count == 4
    assert result.error == "HTTP_500"
    assert [item.attempt_no for item in result.attempts] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_qualification_core_exposes_bounded_failed_case_evidence():
    async def gateway(**_kwargs):
        return {
            "final_content": "wrong answer",
            "gateway_calls": 2,
            "latency_ms": 123,
        }

    result = await run_qualification_core(
        catalog={
            "provider": "new-api",
            "model_id": "glm-5.2",
            "model_kind": "chat",
            "text_generation_eligible": True,
        },
        suites=[
            {
                "suite_key": "diagnostic",
                "version": "1",
                "mode": "qualification",
                "pass_threshold": 0.7,
                "cases": [
                    {
                        "case_key": "failed-exact",
                        "case_version": "1",
                        "active": True,
                        "grader_type": "exact_match",
                        "expected_answer": "right answer",
                    }
                ],
            }
        ],
        gateway=gateway,
    )

    assert result["gateway_calls"] == 2
    case = result["case_results"][0]
    assert case["passed"] is False
    assert case["response_preview"] == "wrong answer"


def test_failed_case_diagnostics_filters_passes_and_bounds_preview():
    diagnostics = _failed_case_diagnostics(
        [
            {"case_key": "ok", "passed": True, "response_preview": "ok"},
            {
                "case_key": "bad",
                "passed": False,
                "score": 0,
                "grader_detail": {"reason": "mismatch"},
                "response_preview": "x" * 2000,
            },
        ]
    )

    assert [item["case_key"] for item in diagnostics] == ["bad"]
    assert len(diagnostics[0]["response_preview"]) == 1200
