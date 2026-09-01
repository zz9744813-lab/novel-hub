"""P1 regression: reasoning-family models must not have their max_tokens
budget swallowed entirely by reasoning (finish=length, final_content_empty)."""
from app.gateway.model_gateway import _generation_controls


def test_deepseek_max_tokens_raised_for_reasoning_headroom():
    controls = _generation_controls(
        "deepseek-v4-flash", max_tokens=16384, reasoning_mode=None
    )
    assert controls["max_tokens"] >= 65536
    assert "thinking" not in controls


def test_deepseek_benchmark_can_disable_reasoning():
    controls = _generation_controls(
        "deepseek-v4-flash", max_tokens=16384, reasoning_mode="disabled"
    )
    assert controls["thinking"] == {"type": "disabled"}
    assert controls["max_tokens"] == 16384


def test_non_deepseek_max_tokens_unchanged():
    controls = _generation_controls(
        "some-other-model", max_tokens=16384, reasoning_mode=None
    )
    assert controls["max_tokens"] == 16384


def test_step3_omits_max_tokens():
    controls = _generation_controls(
        "step-3.7-flash", max_tokens=16384, reasoning_mode=None
    )
    assert "max_tokens" not in controls


def test_glm_thinking_control_preserved():
    controls = _generation_controls(
        "glm-5.2", max_tokens=16384, reasoning_mode="enabled"
    )
    assert controls["thinking"] == {"type": "enabled"}
    assert controls["max_tokens"] == 16384
