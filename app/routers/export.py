from __future__ import annotations

from pathlib import Path

import markdown
from ebooklib import epub
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.db import get_conn
from app.deps import get_templates
from app.security import require_auth
from app.services.library_service import scan_projects
from app.services.markdown_service import read_markdown, safe_slug, utc_now
from app.services.metrics_service import log_operation
from app.services.path_service import list_markdown_files, project_path

router = APIRouter()


@router.get("/export", response_class=HTMLResponse)
def export_page(request: Request) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    templates = get_templates()
    return templates.TemplateResponse(
        "export.html", {"request": request, "projects": scan_projects()}
    )


@router.post("/export/{project}", response_class=HTMLResponse)
def export_project_status(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    chapter_dir = project_path(safe_project) / "chapters"
    if not chapter_dir.exists():
        raise HTTPException(status_code=404, detail="project not found")
    merged = []
    for f in list_markdown_files(chapter_dir):
        fm, body = read_markdown(f)
        merged.append(f"# {fm.get('title', f.stem)}\n\n{body.strip()}\n")
    export_dir = project_path(safe_project) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    export_file = export_dir / f"{safe_project}-{utc_now().strftime('%Y%m%d-%H%M%S')}.txt"
    export_file.write_text("\n\n".join(merged), encoding="utf-8")
    log_operation("export", safe_project, str(export_file))
    templates = get_templates()
    return templates.TemplateResponse(
        "_export_result.html",
        {"request": request, "project": safe_project, "path": str(export_file)},
    )


@router.get("/api/projects/{project}/export")
def api_export(
    request: Request,
    project: str,
    format: str = "epub",
    volume: str = None,
    from_chapter: int = 0,
    to_chapter: int = 999999,
    status: str = None,
) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")

    with get_conn() as conn:
        query = "SELECT * FROM file_index WHERE project=? AND chapter_int >= ? AND chapter_int <= ?"
        params = [safe_project, from_chapter, to_chapter]
        if volume:
            query += " AND volume=?"
            params.append(volume)
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY chapter_int"
        chapters = conn.execute(query, params).fetchall()

        proj_meta = conn.execute(
            "SELECT * FROM project_meta WHERE project=?", (safe_project,)
        ).fetchone()
        vol_meta = None
        if volume:
            vol_meta = conn.execute(
                "SELECT * FROM volumes WHERE project=? AND slug=?",
                (safe_project, volume),
            ).fetchone()

    title = (vol_meta["title"] if vol_meta else None) or safe_project
    author = (proj_meta["author"] if proj_meta else "") or "Anonymous"

    if format == "txt":
        out = []
        for ch in chapters:
            _, body = read_markdown(Path(ch["path"]))
            import re as _re

            body = _re.sub(
                r"\[\[(?:ent_[a-z0-9]+\|)?([^\[\]|#]+?)(?:#[^\[\]]*)?\]\]", r"\1", body
            )
            out.append(f"# {ch['title']}\n\n{body}")
        text = "\n\n---\n\n".join(out)
        return Response(
            content=text.encode("utf-8"),
            media_type="text/plain",
            headers={"Content-Disposition": f'attachment; filename="{title}.txt"'},
        )

    if format == "md":
        out = []
        for ch in chapters:
            _, body = read_markdown(Path(ch["path"]))
            out.append(f"# {ch['title']}\n\n{body}")
        text = "\n\n---\n\n".join(out)
        return Response(
            content=text.encode("utf-8"),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{title}.md"'},
        )

    # EPUB
    book = epub.EpubBook()
    book.set_identifier(f"novelhub-{safe_project}-{volume or 'all'}")
    book.set_title(title)
    book.set_language("zh")
    book.add_author(author)

    spine = ["nav"]
    toc = []

    for ch in chapters:
        _, body = read_markdown(Path(ch["path"]))
        import re as _re

        body = _re.sub(
            r"\[\[(?:ent_[a-z0-9]+\|)?([^\[\]|#]+?)(?:#[^\[\]]*)?\]\]", r"\1", body
        )
        html = markdown.markdown(body)
        c = epub.EpubHtml(
            title=ch["title"], file_name=f"chap_{ch['chapter_int']:05d}.xhtml", lang="zh"
        )
        c.content = f"<h1>{ch['title']}</h1>{html}"
        book.add_item(c)
        spine.append(c)
        toc.append(c)

    book.toc = tuple(toc)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    import io

    buf = io.BytesIO()
    epub.write_epub(buf, book)
    buf.seek(0)

    return Response(
        content=buf.read(),
        media_type="application/epub+zip",
        headers={"Content-Disposition": f'attachment; filename="{title}.epub"'},
    )


@router.get("/projects/{project}/export/all")
def export_project_combined_redirect(request: Request, project: str) -> Response:
    return RedirectResponse(
        url=f"/api/projects/{project}/export?format=md", status_code=303
    )
