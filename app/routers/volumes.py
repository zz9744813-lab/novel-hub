from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.config import NOVELS_ROOT
from app.db import get_conn
from app.security import require_auth
from app.services.markdown_service import safe_slug

router = APIRouter()


@router.get("/projects/{project}/outline", response_class=HTMLResponse)
def project_outline_page_legacy(request: Request, project: str) -> Response:
    """Legacy redirect; new flow uses /projects/{project}/stage/outline."""
    return RedirectResponse(
        url=f"/projects/{project}/stage/outline", status_code=301
    )


@router.put("/api/projects/{project}/volumes/{slug}")
async def api_update_volume(request: Request, project: str, slug: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM volumes WHERE project=? AND slug=?", (safe_project, slug)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE volumes SET title=?, synopsis=?, target_words=?, seq=?
                   WHERE project=? AND slug=?""",
                (
                    data.get("title", slug),
                    data.get("synopsis", ""),
                    int(data.get("target_words") or 0),
                    int(data.get("seq") or 0),
                    safe_project,
                    slug,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO volumes(project, slug, title, synopsis, target_words, seq)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    safe_project,
                    slug,
                    data.get("title", slug),
                    data.get("synopsis", ""),
                    int(data.get("target_words") or 0),
                    int(data.get("seq") or 0),
                ),
            )
    return JSONResponse({"status": "ok"})


@router.post("/api/projects/{project}/volumes/reorder")
async def api_reorder_volumes(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    slugs = data.get("order", [])
    with get_conn() as conn:
        for i, slug in enumerate(slugs, 1):
            conn.execute(
                "UPDATE volumes SET seq=? WHERE project=? AND slug=?",
                (i, safe_project, slug),
            )
    return JSONResponse({"status": "ok"})


@router.get("/api/projects/{project}/volumes")
def api_list_volumes(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    chapters_dir = NOVELS_ROOT / safe_project / "chapters"
    if chapters_dir.exists():
        with get_conn() as conn:
            existing = {
                r["slug"]
                for r in conn.execute(
                    "SELECT slug FROM volumes WHERE project=?", (safe_project,)
                ).fetchall()
            }
            for d in sorted(chapters_dir.iterdir()):
                if d.is_dir() and d.name not in existing:
                    conn.execute(
                        "INSERT INTO volumes(project, slug, title, seq) VALUES (?, ?, ?, ?)",
                        (safe_project, d.name, d.name, len(existing) + 1),
                    )
                    existing.add(d.name)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT v.*,
               (SELECT COUNT(*) FROM file_index WHERE project=v.project AND volume=v.slug) as chapter_count,
               (SELECT COALESCE(SUM(word_count),0) FROM file_index WHERE project=v.project AND volume=v.slug) as word_count
               FROM volumes v WHERE v.project=? ORDER BY v.seq""",
            (safe_project,),
        ).fetchall()
    return JSONResponse({"status": "ok", "volumes": [dict(r) for r in rows]})
