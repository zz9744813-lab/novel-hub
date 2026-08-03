from types import SimpleNamespace
import uuid

import pytest

from app.prompt_runtime import (
    PromptCompileError,
    compile_prompt,
    prompt_snapshot,
)


def _template(system: str, user: str):
    return SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        version=4,
        system_prompt=system,
        user_prompt_template=user,
        template_hash="stored-template-hash",
        variables=["book_profile", "chapter_plan"],
    )


def test_compile_prompt_renders_system_and_user_templates():
    compiled = compile_prompt(
        _template("System {{ book_profile }}", "User {{chapter_plan}}"),
        {"book_profile": "PROFILE", "chapter_plan": "PLAN"},
    )

    assert compiled.system_text == "System PROFILE"
    assert compiled.user_text == "User PLAN"
    assert compiled.rendered_text == "System PROFILE\n\nUser PLAN"
    assert compiled.variables_used == {"book_profile": "PROFILE", "chapter_plan": "PLAN"}
    assert compiled.system_hash
    assert compiled.user_hash


def test_compile_prompt_rejects_missing_variable():
    with pytest.raises(PromptCompileError, match="chapter_plan"):
        compile_prompt(_template("System {{book_profile}}", "User {{chapter_plan}}"), {"book_profile": "PROFILE"})


def test_prompt_snapshot_contains_immutable_template_and_render_hashes():
    compiled = compile_prompt(
        _template("System {{ book_profile }}", "User {{chapter_plan}}"),
        {"book_profile": "PROFILE", "chapter_plan": "PLAN"},
    )
    snapshot = prompt_snapshot(compiled)

    assert snapshot["template_id"] == "00000000-0000-0000-0000-000000000001"
    assert snapshot["template_version"] == 4
    assert snapshot["system_hash"] == compiled.system_hash
    assert snapshot["user_hash"] == compiled.user_hash
    assert snapshot["rendered_hash"] == compiled.rendered_hash
    assert snapshot["variables"] == compiled.variables_used
    assert snapshot["system_text"] == compiled.system_text
    assert snapshot["user_text"] == compiled.user_text
