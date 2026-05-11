from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.deps import get_templates
from app.security import require_auth
from app.services.markdown_service import safe_slug
from app.services.timeline_service import get_project_timeline

router = APIRouter()

@router.get("/projects/{project}/timeline", response_class=HTMLResponse)
def project_timeline_page(request: Request, project: str):
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    items = get_project_timeline(safe_project)
    templates = get_templates()
    return templates.TemplateResponse(
        request,
        "timeline.html",
        {
            "project": safe_project,
            "items": items,
        },
    )

@router.get("/api/projects/{project}/timeline", response_class=JSONResponse)
def project_timeline_api(request: Request, project: str, limit: int = 200):
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    items = get_project_timeline(safe_project, limit=limit)
    return JSONResponse({"status": "ok", "items": items})
