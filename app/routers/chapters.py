from __future__ import annotations

from pathlib import Path

import markdown
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.config import DB_PATH
from app.db import get_conn
from app.deps import get_templates
from app.security import require_auth
from app.services.chapter_service import write_markdown
from app.services.library_service import list_chapters
from app.services.markdown_ext import WikiLinkExtension
from app.services.markdown_service import read_markdown, safe_slug
from app.services.metrics_service import log_operation
from app.services.path_service import chapter_path

router = APIRouter()


@router.post("/projects/{project}/chapters/new")
def create_chapter(
    request: Request,
    project: str,
    title: str = Form("新章节"),
    chapter_number: str = Form(""),
    status: str = Form("draft"),
) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    existing = list_chapters(safe_project)
    idx = len(existing) + 1
    filename = f"{idx:05d}-{safe_slug(title, fallback='chapter')}.md"
    path = chapter_path(safe_project, filename)
    frontmatter = {
        "title": title,
        "chapter": chapter_number or str(idx),
        "status": status,
        "volume": "",
        "tags": [],
        "synopsis": "",
        "notes": "",
        "pov": "",
        "characters": [],
        "locations": [],
        "warnings": [],
        "draft_version": "v1",
    }
    write_markdown(path, frontmatter, "")
    log_operation("create_chapter", str(path), project=safe_project)
    return RedirectResponse(
        url=f"/projects/{safe_project}/editor/{path.name}", status_code=303
    )


@router.delete("/projects/{project}/chapters/{filename}")
def delete_chapter(request: Request, project: str, filename: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    path = chapter_path(safe_project, filename)
    if path.exists():
        path.unlink()
        with get_conn() as conn:
            conn.execute("DELETE FROM file_index WHERE path=?", (str(path),))
            conn.execute("DELETE FROM chapter_fts WHERE path=?", (str(path),))
        log_operation("delete_chapter", str(path), project=safe_project)
    return JSONResponse(
        content={"status": "ok", "new_url": f"/projects/{safe_project}"}
    )


@router.put("/projects/{project}/chapters/{filename}/rename")
async def rename_chapter(request: Request, project: str, filename: str) -> Response:
    require_auth(request)
    data = await request.json()
    new_filename = data.get("name", "")
    if not new_filename.endswith(".md"):
        new_filename += ".md"

    safe_project = safe_slug(project, fallback="project")
    old_p = chapter_path(safe_project, filename)
    new_p = chapter_path(safe_project, new_filename)

    if new_p.exists() and new_p != old_p:
        raise HTTPException(status_code=400, detail="File already exists")

    if old_p.exists():
        old_p.rename(new_p)
        with get_conn() as conn:
            conn.execute(
                "UPDATE file_index SET path=? WHERE path=?", (str(new_p), str(old_p))
            )
            conn.execute(
                "UPDATE chapter_fts SET path=? WHERE path=?", (str(new_p), str(old_p))
            )
        log_operation(
            "rename_chapter", f"{filename} -> {new_p.name}", project=safe_project
        )

    return JSONResponse(
        content={"status": "ok", "new_url": f"/projects/{safe_project}/editor/{new_p.name}"}
    )


@router.get("/projects/{project}/chapters", response_class=HTMLResponse)
def chapters_page_redirect(request: Request, project: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse(
        url=f"/projects/{safe_slug(project, fallback='project')}", status_code=303
    )


@router.get("/projects/{project}/chapters/{filename}/read", response_class=HTMLResponse)
def chapter_read_only(request: Request, project: str, filename: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    path = chapter_path(safe_project, filename)
    if not path.exists():
        raise HTTPException(404)
    fm, body = read_markdown(path)
    html = markdown.markdown(body)
    templates = get_templates()
    return templates.TemplateResponse(
        "_chapter_readonly.html",
        {
            "request": request,
            "project": safe_project,
            "title": fm.get("title", filename),
            "html": html,
        },
    )


@router.get("/projects/{project}/sidebar_chapters")
def sidebar_chapters(
    request: Request, project: str, q: str = "", active: str = ""
) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    # Project detail triggers index sync
    chapters = list_chapters(safe_project, sync=True)

    if q:
        q = q.lower()
        chapters = [
            c
            for c in chapters
            if q in c["title"].lower()
            or q in c.get("chapter", "").lower()
            or q in c.get("volume", "").lower()
        ]
        visible = chapters[:50]
    else:
        active_idx = next((i for i, c in enumerate(chapters) if c["filename"] == active), 0)
        start_idx = max(0, active_idx - 20)
        end_idx = min(len(chapters), active_idx + 21)
        visible = chapters[start_idx:end_idx]

    templates = get_templates()
    return templates.TemplateResponse(
        "_sidebar_chapters.html",
        {
            "request": request,
            "project": safe_project,
            "chapters": visible,
            "filename": active,
        },
    )


@router.post("/projects/{project}/chapters/reorder")
async def reorder_chapters(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    filenames = data.get("order", [])

    if not filenames:
        return JSONResponse(status_code=400, content={"error": "empty order"})

    # Phase 1: rename to .tmp suffix to avoid collisions; remember actual paths
    tmp_paths: list[tuple[str, Path | None]] = []
    for fname in filenames:
        p = chapter_path(safe_project, fname)
        if p.exists():
            tmp = p.with_suffix(".tmp")
            p.rename(tmp)
            tmp_paths.append((fname, tmp))
            # Clean stale index rows pointing at the original path
            with get_conn() as conn:
                conn.execute("DELETE FROM file_index WHERE path=?", (str(p),))
                conn.execute("DELETE FROM chapter_fts WHERE path=?", (str(p),))
        else:
            tmp_paths.append((fname, None))

    # Phase 2: rename .tmp to final; keep file in same volume directory
    for i, (fname, tmp_p) in enumerate(tmp_paths, 1):
        if tmp_p is None or not tmp_p.exists():
            continue
        parts = fname.split("-", 1)
        name_part = parts[1] if len(parts) > 1 else fname
        new_name = f"{i:05d}-{name_part}"
        new_p = tmp_p.parent / new_name  # preserve volume directory
        fm, body = read_markdown(tmp_p)
        fm["chapter"] = str(i)
        # explicit project so write_markdown indexes correctly
        write_markdown(new_p, fm, body, project=safe_project)
        tmp_p.unlink()

    log_operation(
        "reorder_chapters",
        safe_project,
        f"reordered {len(filenames)} chapters",
        project=safe_project,
    )
    return JSONResponse(content={"status": "ok"})


@router.post("/projects/{project}/preview", response_class=HTMLResponse)
def preview_markdown(request: Request, project: str, body: str = Form("")) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    ext = WikiLinkExtension(project=safe_project, db_path=str(DB_PATH))
    html = markdown.markdown(body, extensions=["fenced_code", "tables", ext])
    templates = get_templates()
    return templates.TemplateResponse(
        "_preview.html", {"request": request, "html": html}
    )
