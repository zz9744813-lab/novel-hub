import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.routers.prompt_studio import _evaluate_compatibility


def _template(status="draft", passed=True):
    return MagicMock(
        id=uuid.uuid4(),
        template_key="chapter_planner:system:global",
        agent_role="chapter_planner",
        scope_type="system",
        scope_id=None,
        version=2,
        status=status,
        name="test",
        system_prompt="System {{chapter_plan}}",
        user_prompt_template="User {{chapter_plan}}",
        variables=["chapter_plan"],
        allowed_context_kinds=[],
        required_context_kinds=[],
        forbidden_context_kinds=[],
        output_contract_key="chapter_planner",
        input_contract_key=None,
        last_test_passed=passed,
    )


def test_active_template_is_not_eligible_for_in_place_edit():
    template = _template(status="active")
    assert template.status == "active"
    assert _evaluate_compatibility(template)["status"] == "active"


def test_missing_runtime_variable_is_an_activation_error():
    from app.prompt_runtime import PromptCompileError, compile_prompt

    with pytest.raises(PromptCompileError):
        compile_prompt(_template(), {})
