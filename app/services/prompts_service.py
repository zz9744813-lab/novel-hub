"""
Three-layer prompt composition: global -> stage -> task.

Storage uses the existing settings table:
- global: "prompt:global"
- stage: "prompt:{project}:{stage}"
"""
from __future__ import annotations


GLOBAL_KEY = "prompt:global"


def stage_key(project: str, stage: str) -> str:
    return f"prompt:{project}:{stage}"


def stage_done_key(project: str, stage: str) -> str:
    return f"stage_done:{project}:{stage}"


def get_global_prompt(get_setting_fn) -> str:
    return (get_setting_fn(GLOBAL_KEY, "") or "").strip()


def get_stage_prompt(get_setting_fn, project: str, stage: str) -> str:
    return (get_setting_fn(stage_key(project, stage), "") or "").strip()


def set_global_prompt(set_setting_fn, content: str) -> None:
    set_setting_fn(GLOBAL_KEY, content or "")


def set_stage_prompt(set_setting_fn, project: str, stage: str, content: str) -> None:
    set_setting_fn(stage_key(project, stage), content or "")


def is_stage_done(get_setting_fn, project: str, stage: str) -> bool:
    return get_setting_fn(stage_done_key(project, stage), "0") == "1"


def mark_stage_done(set_setting_fn, project: str, stage: str, done: bool) -> None:
    set_setting_fn(stage_done_key(project, stage), "1" if done else "0")


def build_layered_prompt(
    get_setting_fn,
    project: str,
    stage: str,
    base_prompt: str,
) -> str:
    """Compose global/stage/task prompt layers, skipping empty layers."""
    parts: list[str] = []
    global_prompt = get_global_prompt(get_setting_fn)
    if global_prompt:
        parts.append(f"[全局约束]\n{global_prompt}")
    stage_prompt = get_stage_prompt(get_setting_fn, project, stage)
    if stage_prompt:
        parts.append(f"[本阶段约束]\n{stage_prompt}")
    task_prompt = (base_prompt or "").strip()
    if task_prompt:
        parts.append(f"[本次任务]\n{task_prompt}")
    return "\n\n".join(parts)
