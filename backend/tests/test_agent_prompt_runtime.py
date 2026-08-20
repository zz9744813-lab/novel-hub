import json
from types import SimpleNamespace

from app.agents.caller import _prompt_variables


def test_prompt_variables_unpack_json_user_content():
    values = _prompt_variables(
        '{"chapter_plan": {"scene_no": 1}, "context_package": "CTX"}',
        assembly_manifest={"manifest": "M"},
    )

    assert values["chapter_plan"] == {"scene_no": 1}
    assert values["context_package"] == "CTX"
    assert values["assembly_manifest"] == {"manifest": "M"}
    assert values["user_content"] == '{"chapter_plan": {"scene_no": 1}, "context_package": "CTX"}'


def test_prompt_variables_preserves_non_json_user_content():
    values = _prompt_variables("plain instruction", assembly_manifest=None)

    assert values["user_instruction"] == "plain instruction"
    assert values["user_content"] == "plain instruction"
