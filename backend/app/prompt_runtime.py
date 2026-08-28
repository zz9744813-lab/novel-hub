"""Prompt Studio runtime compiler and immutable per-attempt snapshot helpers."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any


_VARIABLE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class PromptCompileError(ValueError):
    """Raised when a template cannot be rendered with the supplied variables."""


@dataclass(frozen=True)
class CompiledPrompt:
    template_id: Any
    template_version: int
    system_template: str
    user_template: str
    system_text: str
    user_text: str
    rendered_text: str
    variables_used: dict[str, Any]
    system_hash: str
    user_hash: str
    rendered_hash: str


def _render(text: str, variables: dict[str, Any], label: str) -> tuple[str, set[str]]:
    source = text or ""
    names = set(_VARIABLE.findall(source))
    missing = sorted(name for name in names if name not in variables)
    if missing:
        raise PromptCompileError(f"missing {label} variables: {', '.join(missing)}")

    # Check the ORIGINAL template for dangling/unknown braces AFTER all
    # legitimate {{name}} placeholders are removed. Scanning the raw template
    # (not the substituted text) keeps JSON values -- which legitimately
    # contain adjacent }} -- from being misread as unresolved placeholders.
    residue = _VARIABLE.sub("", source)
    if "{{" in residue or "}}" in residue:
        raise PromptCompileError(f"unresolved placeholder in {label} template")

    rendered = _VARIABLE.sub(lambda match: str(variables[match.group(1)]), source)
    return rendered, names


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _version_number(value: Any) -> int:
    if isinstance(value, int):
        return value
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else 0


def compile_prompt(template: Any, variables: dict[str, Any]) -> CompiledPrompt:
    """Render both Studio templates from one explicit variable package."""
    if not isinstance(variables, dict):
        raise PromptCompileError("variables must be an object")
    system_text, system_names = _render(template.system_prompt or "", variables, "system")
    user_text, user_names = _render(template.user_prompt_template or "", variables, "user")
    used = {name: variables[name] for name in sorted(system_names | user_names)}
    rendered_text = f"{system_text}\n\n{user_text}" if user_text else system_text
    return CompiledPrompt(
        template_id=template.id,
        template_version=_version_number(template.version),
        system_template=template.system_prompt or "",
        user_template=template.user_prompt_template or "",
        system_text=system_text,
        user_text=user_text,
        rendered_text=rendered_text,
        variables_used=used,
        system_hash=_sha256(system_text),
        user_hash=_sha256(user_text),
        rendered_hash=_sha256(rendered_text),
    )


def prompt_snapshot(compiled: CompiledPrompt) -> dict[str, Any]:
    """Return JSON-safe immutable evidence for the exact compiled prompt."""
    return {
        "template_id": str(compiled.template_id),
        "template_version": compiled.template_version,
        "system_template": compiled.system_template,
        "user_template": compiled.user_template,
        "system_hash": compiled.system_hash,
        "user_hash": compiled.user_hash,
        "rendered_hash": compiled.rendered_hash,
        "variables": compiled.variables_used,
        "system_text": compiled.system_text,
        "user_text": compiled.user_text,
    }
