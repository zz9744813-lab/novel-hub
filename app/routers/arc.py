from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.deps import get_templates
from app.security import require_auth
from app.services.markdown_service import safe_slug
from app.services.arc_service import get_entity_arc

router = APIRouter()


@router.get("/projects/{project}/entities/{ent_id}/arc", response_class=HTMLResponse)
def get_entity_arc_page(request: Request, project: str, ent_id: str):
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")

    try:
        arc = get_entity_arc(ent_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="entity not found")

    if arc["entity"]["project"] != safe_project:
        raise HTTPException(status_code=404, detail="entity not found")

    return get_templates().TemplateResponse(
        request,
        "entity_arc.html",
        {
            "project": safe_project,
            "entity": arc["entity"],
            "events": arc["events"],
            "field_groups": arc["field_groups"],
            "stats": arc["stats"],
        }
    )


@router.get("/api/entities/{ent_id}/arc", response_class=JSONResponse)
def get_entity_arc_api(request: Request, ent_id: str):
    require_auth(request)

    try:
        arc = get_entity_arc(ent_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="entity not found")

    return {
        "status": "ok",
        "entity": arc["entity"],
        "events": arc["events"],
        "field_groups": arc["field_groups"],
        "stats": arc["stats"],
    }
