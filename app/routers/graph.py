from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from app.deps import get_templates
from app.security import require_auth
from app.services.markdown_service import safe_slug
from app.services.graph_service import get_project_graph


router = APIRouter()


@router.get("/projects/{project}/graph", response_class=HTMLResponse)
def get_graph_page(request: Request, project: str, kind: str = "", q: str = "") -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    templates = get_templates()

    graph = get_project_graph(safe_project, kind or None, q or None)

    return templates.TemplateResponse(
        request,
        "graph.html",
        {
            "project": project,  # Original project string for display/URLs
            "graph": graph,
            "kind": kind,
            "q": q
        }
    )


@router.get("/api/projects/{project}/graph")
def api_get_graph(request: Request, project: str, kind: str = "", q: str = "") -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")

    graph = get_project_graph(safe_project, kind or None, q or None)

    return JSONResponse({
        "status": "ok",
        **graph
    })
