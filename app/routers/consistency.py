from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import feature_enabled
from app.deps import get_templates
from app.security import require_auth
from app.services.library_service import list_chapters
from app.services.consistency_report_service import get_consistency_summary, list_consistency_reports
from app.services.consistency_service import run_consistency_check
from app.services.markdown_service import safe_slug
from app.services.path_service import chapter_path

router = APIRouter(tags=["consistency"])


@router.get("/projects/{project}/consistency", response_class=HTMLResponse)
async def consistency_page(request: Request, project: str):
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")

    reports = list_consistency_reports(safe_project)
    summary = get_consistency_summary(safe_project)
    ai_check_enabled = feature_enabled("ai_check")

    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "consistency.html",
        {
            "project": safe_project,
            "reports": reports,
            "summary": summary,
            "ai_check_enabled": ai_check_enabled,
        },
    )


@router.get("/api/projects/{project}/consistency", response_class=JSONResponse)
async def consistency_api(request: Request, project: str):
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")

    reports = list_consistency_reports(safe_project)
    summary = get_consistency_summary(safe_project)

    return {
        "status": "ok",
        "summary": summary,
        "reports": reports,
    }


@router.post("/api/projects/{project}/consistency/run", response_class=JSONResponse)
async def consistency_run_api(request: Request, project: str, background_tasks: BackgroundTasks):
    require_auth(request)

    if not feature_enabled("ai_check"):
        raise HTTPException(status_code=400, detail="ai_check disabled")

    safe_project = safe_slug(project, fallback="project")

    try:
        data = await request.json()
    except Exception:
        data = {}

    filename = data.get("filename")

    if filename:
        path = chapter_path(safe_project, filename)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Chapter not found")

        background_tasks.add_task(run_consistency_check, safe_project, str(path))
        queued = 1
    else:
        chapters = list_chapters(safe_project, sync=True)
        for chapter in chapters:
            background_tasks.add_task(run_consistency_check, safe_project, chapter["path"])
        queued = len(chapters)

    return {"status": "ok", "queued": queued}
