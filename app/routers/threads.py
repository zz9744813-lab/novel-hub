from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.deps import get_templates
from app.security import require_auth
from app.services.markdown_service import safe_slug
from app.services.threads_service import (
    get_threads_board,
    create_thread,
    update_thread,
    delete_thread
)

router = APIRouter()

@router.get("/projects/{project}/threads-board", response_class=HTMLResponse)
def threads_board_page(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    board = get_threads_board(safe_project)

    templates = get_templates()
    return templates.TemplateResponse(
        "threads_board.html",
        {
            "request": request,
            "project": project,
            "board": board,
        }
    )

@router.get("/api/projects/{project}/threads-board")
def api_threads_board(request: Request, project: str) -> JSONResponse:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    board = get_threads_board(safe_project)
    return JSONResponse(content={"status": "ok", **board})

@router.post("/api/projects/{project}/threads")
async def api_create_thread(request: Request, project: str) -> JSONResponse:
    require_auth(request)
    data = await request.json()

    title = data.get("title")
    if not title:
        raise HTTPException(status_code=400, detail="title required")

    status = data.get("status", "open")
    priority = data.get("priority", "normal")
    body = data.get("body", "")

    thread = create_thread(project, title, body, status, priority)

    return JSONResponse(content={"status": "ok", "thread": thread})

@router.put("/api/projects/{project}/threads/{filename}")
async def api_update_thread(request: Request, project: str, filename: str) -> JSONResponse:
    require_auth(request)
    data = await request.json()

    try:
        thread = update_thread(project, filename, data)
        return JSONResponse(content={"status": "ok", "thread": thread})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="thread not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/api/projects/{project}/threads/{filename}")
def api_delete_thread(request: Request, project: str, filename: str) -> JSONResponse:
    require_auth(request)

    try:
        delete_thread(project, filename)
        return JSONResponse(content={"status": "ok"})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="thread not found")
