from __future__ import annotations

import markdown
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.config import DB_PATH, VAULT_ROOT
from app.db import get_conn
from app.deps import get_templates
from app.security import require_auth
from app.services.markdown_ext import WikiLinkExtension
from app.services.markdown_service import _ensure_under_root, read_markdown, safe_slug
from app.services.metrics_service import log_operation
from app.services.path_service import project_path

router = APIRouter()


@router.get("/projects/{project}/characters", response_class=HTMLResponse)
def characters_page_legacy(request: Request, project: str) -> Response:
    """Legacy redirect; new flow uses /projects/{project}/stage/characters."""
    return RedirectResponse(
        url=f"/projects/{project}/stage/characters", status_code=301
    )


@router.get("/projects/{project}/world", response_class=HTMLResponse)
def world_page_legacy(request: Request, project: str) -> Response:
    """Legacy redirect; new flow uses /projects/{project}/stage/worldview."""
    return RedirectResponse(
        url=f"/projects/{project}/stage/worldview", status_code=301
    )


@router.get("/projects/{project}/notes/{folder}/{filename}", response_class=HTMLResponse)
def note_preview(
    request: Request, project: str, folder: str, filename: str
) -> Response:
    safe_project = safe_slug(project, fallback="project")
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    if folder not in {"characters", "world"}:
        raise HTTPException(status_code=400, detail="invalid folder")
    safe_project = safe_slug(project, fallback="project")
    p = (
        project_path(safe_project)
        / folder
        / (safe_slug(filename.replace(".md", "")) + ".md")
    )
    p = _ensure_under_root(p, VAULT_ROOT)
    if not p.exists():
        raise HTTPException(status_code=404, detail="not found")
    _, body = read_markdown(p)
    ext = WikiLinkExtension(project=safe_project, db_path=str(DB_PATH))
    html = markdown.markdown(body, extensions=["fenced_code", "tables", ext])
    templates = get_templates()
    return templates.TemplateResponse(
        "_note_preview.html", {"request": request, "title": p.stem, "html": html}
    )


@router.delete("/projects/{project}/notes/{folder}/{filename}")
def delete_note(request: Request, project: str, folder: str, filename: str) -> Response:
    require_auth(request)
    if folder not in {"characters", "world"}:
        raise HTTPException(status_code=400, detail="invalid folder")
    safe_project = safe_slug(project, fallback="project")
    p = project_path(safe_project) / folder / filename
    p = _ensure_under_root(p, VAULT_ROOT)
    if p.exists():
        p.unlink()
        with get_conn() as conn:
            conn.execute("DELETE FROM file_index WHERE path=?", (str(p),))
        log_operation("delete_note", str(p))
    return JSONResponse(
        content={"status": "ok", "new_url": f"/projects/{safe_project}/{folder}"}
    )


@router.put("/projects/{project}/notes/{folder}/{filename}/rename")
async def rename_note(
    request: Request, project: str, folder: str, filename: str
) -> Response:
    require_auth(request)
    if folder not in {"characters", "world"}:
        raise HTTPException(status_code=400)
    data = await request.json()
    new_filename = safe_slug(data.get("name", "").replace(".md", "")) + ".md"

    safe_project = safe_slug(project, fallback="project")
    old_p = project_path(safe_project) / folder / filename
    old_p = _ensure_under_root(old_p, VAULT_ROOT)
    new_p = project_path(safe_project) / folder / new_filename
    new_p = _ensure_under_root(new_p, VAULT_ROOT)

    if new_p.exists() and new_p != old_p:
        raise HTTPException(status_code=400, detail="File already exists")

    if old_p.exists():
        old_p.rename(new_p)
        with get_conn() as conn:
            conn.execute(
                "UPDATE file_index SET path=? WHERE path=?", (str(new_p), str(old_p))
            )
        log_operation("rename_note", f"{old_p.name} -> {new_p.name}")

    return JSONResponse(
        content={"status": "ok", "new_url": f"/projects/{safe_project}/{folder}"}
    )
