from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.config import NOVELS_ROOT
from app.db import get_conn, get_setting, set_setting
from app.deps import get_templates
from app.security import require_auth
from app.services.markdown_service import safe_slug, write_atomic
from app.services.library_service import list_chapters
from app.services.metrics_service import log_operation
from app.services.stage_service import is_valid_stage, STAGE_LABELS
from app.services.prompts_service import (
    get_stage_prompt,
    set_stage_prompt,
    set_global_prompt,
    mark_stage_done,
    is_stage_done,
)

router = APIRouter()


# ===== Workflow prompt slots =====

@router.get("/api/prompts/{project}/{stage}")
def api_get_prompt(request: Request, project: str, stage: str) -> Response:
    require_auth(request)
    if not is_valid_stage(stage):
        raise HTTPException(400, f"unknown stage: {stage}")
    safe_project = safe_slug(project, fallback="project")
    return JSONResponse({"status": "ok", "content": get_stage_prompt(get_setting, safe_project, stage)})


@router.put("/api/prompts/{project}/{stage}")
async def api_set_prompt(request: Request, project: str, stage: str) -> Response:
    require_auth(request)
    if not is_valid_stage(stage):
        raise HTTPException(400, f"unknown stage: {stage}")
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    set_stage_prompt(set_setting, safe_project, stage, data.get("content", ""))
    log_operation("set_stage_prompt", target=f"{safe_project}:{stage}")
    return JSONResponse({"status": "ok"})


@router.put("/api/prompts/global")
async def api_set_global_prompt(request: Request) -> Response:
    require_auth(request)
    data = await request.json()
    set_global_prompt(set_setting, data.get("content", ""))
    log_operation("set_global_prompt")
    return JSONResponse({"status": "ok"})


# ===== Stage status (mark done) =====

@router.put("/api/stages/{project}/{stage}/done")
async def api_mark_stage_done(request: Request, project: str, stage: str) -> Response:
    require_auth(request)
    if not is_valid_stage(stage):
        raise HTTPException(400, f"unknown stage: {stage}")
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    mark_stage_done(set_setting, safe_project, stage, bool(data.get("done", True)))
    return JSONResponse({"status": "ok"})


# ===== Workflow stage page dispatcher =====

@router.get("/projects/{project}/stage/{stage}", response_class=HTMLResponse)
def stage_page(request: Request, project: str, stage: str) -> Response:
    """Unified workflow stage entry. Each stage renders its own template."""
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    if not is_valid_stage(stage):
        raise HTTPException(404, f"unknown stage: {stage}")

    templates = get_templates()
    stage_prompt = get_stage_prompt(get_setting, safe_project, stage)
    stage_done = is_stage_done(get_setting, safe_project, stage)
    common_ctx = {
        "request": request,
        "project": safe_project,
        "stage": stage,
        "stage_label_zh": STAGE_LABELS[stage],
        "stage_prompt": stage_prompt,
        "stage_done": stage_done,
    }

    if stage == "premise":
        f = NOVELS_ROOT / safe_project / ".workflow" / "premise.md"
        content = f.read_text(encoding="utf-8") if f.exists() else ""
        return templates.TemplateResponse("stage_premise.html", {**common_ctx, "content": content})

    if stage == "worldview":
        f = NOVELS_ROOT / safe_project / ".workflow" / "worldview.md"
        content = f.read_text(encoding="utf-8") if f.exists() else ""
        return templates.TemplateResponse("stage_worldview.html", {**common_ctx, "content": content})

    if stage == "characters":
        with get_conn() as conn:
            entities = conn.execute(
                "SELECT * FROM entities WHERE project=? AND kind='character' ORDER BY name",
                (safe_project,),
            ).fetchall()
        return templates.TemplateResponse(
            "stage_characters.html",
            {**common_ctx, "entities": [dict(e) for e in entities]},
        )

    if stage == "outline":
        with get_conn() as conn:
            volumes = conn.execute(
                "SELECT * FROM volumes WHERE project=? ORDER BY seq",
                (safe_project,),
            ).fetchall()
        return templates.TemplateResponse(
            "stage_outline.html",
            {**common_ctx, "volumes": [dict(v) for v in volumes]},
        )

    if stage == "chapter_outline":
        chapters = []
        for chapter in list_chapters(safe_project, sync=False):
            item = dict(chapter)
            modified = item.get("modified")
            if isinstance(modified, datetime):
                item["modified"] = modified.isoformat()
            chapters.append(item)
        return templates.TemplateResponse(
            "stage_chapter_outline.html",
            {**common_ctx, "chapters": chapters},
        )

    if stage == "writing":
        return RedirectResponse(url=f"/projects/{safe_project}", status_code=303)

    raise HTTPException(404)


@router.put("/api/projects/{project}/workflow/{stage}/content")
async def api_save_stage_content(request: Request, project: str, stage: str) -> Response:
    """Save markdown content for premise/worldview stages."""
    require_auth(request)
    if not is_valid_stage(stage) or stage not in ("premise", "worldview"):
        raise HTTPException(400, f"stage {stage} has no markdown content")
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    content = data.get("content", "")
    folder = NOVELS_ROOT / safe_project / ".workflow"
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{stage}.md"
    write_atomic(f, content)
    log_operation("save_stage_content", target=f"{safe_project}:{stage}", value=len(content))
    return JSONResponse({"status": "ok", "saved_chars": len(content)})
