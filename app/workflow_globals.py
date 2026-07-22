from __future__ import annotations

from fastapi.templating import Jinja2Templates

from app.config import NOVELS_ROOT
from app.db import get_conn, get_setting
from app.services.stage_service import (
    STAGES,
    STAGE_KEYS,
    stage_label,
    compute_stage_status,
    next_actionable_stage,
)
from app.services.prompts_service import is_stage_done


def project_stage_status_map(project: str) -> dict[str, str]:
    """Return {stage_key: 'todo'|'in_progress'|'done'} for the project."""
    out: dict[str, str] = {}
    with get_conn() as conn:
        for stage_key in STAGE_KEYS:
            done = is_stage_done(get_setting, project, stage_key)
            out[stage_key] = compute_stage_status(stage_key, project, NOVELS_ROOT, conn, done)
    return out


def project_next_stage(project: str) -> tuple[str, str]:
    with get_conn() as conn:
        return next_actionable_stage(
            project,
            NOVELS_ROOT,
            conn,
            lambda stage: is_stage_done(get_setting, project, stage),
        )


def register_workflow_globals(templates: Jinja2Templates) -> None:
    templates.env.globals["WORKFLOW_STAGES"] = STAGES
    templates.env.globals["stage_label"] = stage_label
    templates.env.globals["project_stage_status_map"] = project_stage_status_map
    templates.env.globals["project_next_stage"] = project_next_stage
