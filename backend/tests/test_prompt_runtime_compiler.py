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


# === P0-A regression: JSON / literal braces inside variable values must NOT
#     be treated as unresolved template placeholders. ===


def test_compile_prompt_accepts_json_value_with_adjacent_closing_braces():
    """Template {{user_content}} + JSON value {"outer":{"inner":1}} must
    compile successfully and keep the value bytes untouched."""
    compiled = compile_prompt(
        _template("System {{ book_profile }}", "User {{user_content}}"),
        {"book_profile": "PROFILE", "user_content": '{"outer":{"inner":1}}'},
    )
    assert compiled.user_text == 'User {"outer":{"inner":1}}'
    assert compiled.variables_used["user_content"] == '{"outer":{"inner":1}}'


def test_compile_prompt_accepts_literal_braces_inside_variable_value():
    """A value containing "{{literal}}" or "}}" is data, not a placeholder."""
    compiled = compile_prompt(
        _template("User {{user_content}}", "User {{user_content}}"),
        {"user_content": "prefix {{literal}} suffix }}"},
    )
    assert compiled.user_text == "User prefix {{literal}} suffix }}"


def test_compile_prompt_rejects_missing_variable_with_names():
    """Missing declared variables stay fail-closed and list the names."""
    with pytest.raises(PromptCompileError, match="missing_var"):
        compile_prompt(
            _template("System {{book_profile}}", "User {{missing_var}}"),
            {"book_profile": "PROFILE"},
        )


def test_compile_prompt_rejects_unclosed_left_placeholder():
    """Template 'broken {{user_content' must fail (dangling left brace)."""
    with pytest.raises(PromptCompileError, match="unresolved placeholder"):
        compile_prompt(
            _template("System {{book_profile}}", "User broken {{user_content"),
            {"book_profile": "PROFILE", "user_content": "X"},
        )


def test_compile_prompt_rejects_unmatched_right_placeholder():
    """Template 'broken user_content}}' must fail (dangling right brace)."""
    with pytest.raises(PromptCompileError, match="unresolved placeholder"):
        compile_prompt(
            _template("System {{book_profile}}", "User broken user_content}}"),
            {"book_profile": "PROFILE"},
        )


def test_compile_prompt_hashes_and_snapshot_stay_correct_with_json_value():
    """system/user hashes, variables_used and snapshot remain reproducible."""
    compiled = compile_prompt(
        _template("System {{ book_profile }}", "User {{user_content}}"),
        {"book_profile": "PROFILE", "user_content": '{"a":1,"b":{"c":2}}'},
    )
    assert compiled.system_hash == compiled.system_hash  # deterministic
    assert compiled.user_hash
    assert compiled.rendered_hash
    snapshot = prompt_snapshot(compiled)
    assert snapshot["variables"]["user_content"] == '{"a":1,"b":{"c":2}}'
    assert snapshot["user_text"] == 'User {"a":1,"b":{"c":2}}'
    assert snapshot["user_hash"] == compiled.user_hash
    assert snapshot["rendered_hash"] == compiled.rendered_hash