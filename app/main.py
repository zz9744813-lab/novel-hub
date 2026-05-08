from __future__ import annotations

import shutil
import gzip
from datetime import datetime, timezone
from pathlib import Path
import json
import hashlib
from typing import Any, List, Dict, Tuple
from contextlib import asynccontextmanager

from ebooklib import epub
import markdown
from fastapi import FastAPI, Form, HTTPException, Request, BackgroundTasks
from starlette_csrf import CSRFMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.services.ai_client import generate_ai_content, generate_ai_content_stream
from app.services.ai_context import build_context
from app.services.wiki_link import update_entity_refs
from app.services.markdown_ext import WikiLinkExtension
from starlette.middleware.sessions import SessionMiddleware

from app.config import (
    BASE_DIR, VAULT_ROOT, NOVELS_ROOT, BACKUP_ROOT, DB_PATH,
    ADMIN_PASSWORD, SECRET_KEY, ENCRYPTION_KEY, APP_ENV,
    DAILY_GOAL_WORDS, PROJECT_GOAL_WORDS,
    env_bool, FEATURES, feature_enabled, require_feature, validate_runtime_config,
)
from app.db import (
    get_conn, get_setting, set_setting, clear_setting,
    set_setting_encrypted, get_setting_decrypted,
)
from app.security import require_auth
from app.labels import status_label, kind_label
from app.templating import create_templates
from app.services.markdown_service import (
    utc_now,
    safe_slug,
    _ensure_under_root,
    FRONTMATTER_PATTERN,
    parse_frontmatter,
    dump_frontmatter,
    read_markdown,
    write_atomic,
    count_words,
    parse_csv,
)
from app.services.path_service import chapter_path, project_path, list_markdown_files
from app.services.snapshot_service import backup_file
from app.services.project_service import get_project_meta, set_project_meta
from app.services.chapter_service import _infer_kind, normalize_meta, write_markdown
from app.services.library_service import (
    _reindex_notes_for_project,
    list_chapters,
    list_notes,
    scan_projects,
)
from app.services.metrics_service import log_operation, compute_trend, get_project_stats
from app.schema import init_db
from app.routers import health as health_router
from app.routers.auth import create_router as create_auth_router
from app.routers import settings as settings_router
from app.routers import dashboard as dashboard_router
from app.routers import projects as projects_router
from app.routers import chapters as chapters_router
from app.constants import STATUS_ORDER

validate_runtime_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    NOVELS_ROOT.mkdir(parents=True, exist_ok=True)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    init_db()
    needs_note_rebuild = get_setting("fts_needs_rebuild", "0") == "1"
    for p in [item.name for item in NOVELS_ROOT.iterdir() if item.is_dir()]:
        list_chapters(p, sync=True)
        if needs_note_rebuild:
            _reindex_notes_for_project(p)
    if needs_note_rebuild:
        set_setting("fts_needs_rebuild", "0")
    yield
    # shutdown: nothing


app = FastAPI(title="Novel Hub", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

ENABLE_CSRF = env_bool("NOVELHUB_ENABLE_CSRF", APP_ENV == "production")

if ENABLE_CSRF:
    app.add_middleware(
        CSRFMiddleware,
        secret=SECRET_KEY,
        exempt_urls=[r"^/api/.*", r"^/login$"],
    )

limiter = Limiter(key_func=get_remote_address, enabled=APP_ENV == "production")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda r, e: JSONResponse({"detail": "too many attempts"}, status_code=429))

app.include_router(health_router.router)
app.include_router(create_auth_router(limiter))
app.include_router(settings_router.router)
app.include_router(dashboard_router.router)
app.include_router(projects_router.router)
app.include_router(chapters_router.router)

class CacheStaticFiles(StaticFiles):
    def is_not_modified(self, response_headers, request_headers) -> bool:
        response_headers["Cache-Control"] = "public, max-age=31536000"
        return super().is_not_modified(response_headers, request_headers)

app.mount("/static", CacheStaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = create_templates()

from app.deps import configure_runtime
configure_runtime(templates_obj=templates, limiter_obj=limiter)

# Workflow stages
from app.services.stage_service import (
    STAGES,
    STAGE_KEYS,
    stage_label as _stage_label,
    compute_stage_status as _compute_stage_status,
    next_actionable_stage as _next_actionable_stage,
)
from app.services.prompts_service import (
    is_stage_done as _is_stage_done,
    get_global_prompt as _get_global_prompt,
    get_stage_prompt as _get_stage_prompt,
    set_global_prompt as _set_global_prompt,
    set_stage_prompt as _set_stage_prompt,
    mark_stage_done as _mark_stage_done,
    build_layered_prompt as _build_layered_prompt,
)

templates.env.globals["WORKFLOW_STAGES"] = STAGES
templates.env.globals["stage_label"] = _stage_label


def project_stage_status_map(project: str) -> dict[str, str]:
    """Return {stage_key: 'todo'|'in_progress'|'done'} for the project."""
    out: dict[str, str] = {}
    with get_conn() as conn:
        for stage_key in STAGE_KEYS:
            done = _is_stage_done(get_setting, project, stage_key)
            out[stage_key] = _compute_stage_status(stage_key, project, NOVELS_ROOT, conn, done)
    return out


def project_next_stage(project: str) -> tuple[str, str]:
    with get_conn() as conn:
        return _next_actionable_stage(
            project,
            NOVELS_ROOT,
            conn,
            lambda stage: _is_stage_done(get_setting, project, stage),
        )


templates.env.globals["project_stage_status_map"] = project_stage_status_map
templates.env.globals["project_next_stage"] = project_next_stage






























@app.get("/projects/{project}/editor/{filename}", response_class=HTMLResponse)
def editor_page(request: Request, project: str, filename: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    safe_project = safe_slug(project, fallback="project")
    path = chapter_path(safe_project, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="chapter not found")
    fm, body = read_markdown(path)
    meta = normalize_meta(fm, path.stem, project=safe_project)
    # Editor uses DB index directly without scanning
    chapters = list_chapters(safe_project, sync=False)
    active = next((c for c in chapters if c["filename"] == path.name), None)
    
    active_idx = next((i for i, c in enumerate(chapters) if c["filename"] == path.name), 0)
    start_idx = max(0, active_idx - 20)
    end_idx = min(len(chapters), active_idx + 21)
    visible_chapters = chapters[start_idx:end_idx]

    proj_meta = get_project_meta(safe_project)
    
    # T2.7 Sidebar Data
    with get_conn() as conn:
        mentioned = conn.execute(
            """SELECT DISTINCT e.* FROM entities e 
               JOIN entity_refs er ON e.id = er.entity_id 
               WHERE er.chapter_path = ?""", (str(path),)).fetchall()
        
        snapshots = conn.execute(
            "SELECT id, created_at, label FROM snapshots WHERE chapter_path = ? ORDER BY created_at DESC LIMIT 50",
            (str(path),)).fetchall()

    from app.services.prompts_service import get_stage_prompt
    return templates.TemplateResponse(
        "editor.html",
        {
            "request": request,
            "project": safe_project,
            "filename": path.name,
            "frontmatter": meta,
            "body": body,
            "chapters": visible_chapters,
            "active": active,
            "project_words": sum(c["word_count"] for c in chapters),
            "goal": proj_meta.get("target_words", PROJECT_GOAL_WORDS),
            "mtime": path.stat().st_mtime,
            "mentioned_entities": [dict(e) for e in mentioned],
            "snapshots": [dict(s) for s in snapshots],
            "writing_prompt": get_stage_prompt(get_setting, safe_project, "writing"),
        },
    )




@app.post("/projects/{project}/editor/{filename}", response_class=HTMLResponse)
def save_chapter(
    request: Request,
    project: str,
    filename: str,
    background_tasks: BackgroundTasks,
    title: str = Form(""),
    chapter: str = Form(""),
    status: str = Form("draft"),
    volume: str = Form(""),
    tags: str = Form(""),
    synopsis: str = Form(""),
    notes: str = Form(""),
    pov: str = Form(""),
    characters: str = Form(""),
    locations: str = Form(""),
    warnings: str = Form(""),
    draft_version: str = Form(""),
    body: str = Form(""),
    loaded_mtime: str = Form(""),
    force: bool = Form(False),
) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    path = chapter_path(safe_project, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="chapter not found")

    # M11: Conflict detection
    if loaded_mtime:
        try:
            loaded_mtime = float(loaded_mtime)
            current_mtime = path.stat().st_mtime
            if abs(current_mtime - loaded_mtime) > 0.5 and not force:
                current_content = path.read_text(encoding="utf-8") if path.exists() else ""
                return templates.TemplateResponse(
                    "_save_result.html",
                    {
                        "request": request,
                        "error": "文件已被其他来源修改，请先查看冲突再决定是否覆盖。",
                        "error_code": "chapter_conflict",
                        "current_mtime": current_mtime,
                        "loaded_mtime": loaded_mtime,
                        "current_hash": hashlib.sha256(current_content.encode("utf-8")).hexdigest(),
                        "loaded_hash": "",
                    },
                )
        except (ValueError, OSError):
            pass

    expected_path = chapter_path(safe_project, filename, volume)
    if path != expected_path:
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        path.rename(expected_path)
        with get_conn() as conn:
            conn.execute("UPDATE file_index SET path=? WHERE path=?", (str(expected_path), str(path)))
            conn.execute("UPDATE chapter_fts SET path=? WHERE path=?", (str(expected_path), str(path)))
        path = expected_path

    old_words = 0
    try:
        _, old_body = read_markdown(path)
        old_words = count_words(old_body)
    except Exception:
        pass

    frontmatter = {
        "title": title,
        "chapter": chapter,
        "status": status,
        "volume": volume,
        "tags": parse_csv(tags),
        "synopsis": synopsis,
        "notes": notes,
        "pov": pov,
        "characters": parse_csv(characters),
        "locations": parse_csv(locations),
        "warnings": parse_csv(warnings),
        "draft_version": draft_version,
    }

    new_words = count_words(body)
    words_added = new_words - old_words
    if words_added < 0:
        words_added = 0 # Don't record negative progress in trend

    if force and path.exists():
        backup_file(path, label="pre_overwrite")
    write_markdown(path, frontmatter, body)
    log_operation("save", str(path), f"words_added={words_added}", value=words_added, project=safe_project)
    new_mtime = path.stat().st_mtime
    if feature_enabled("ai_check"):
        background_tasks.add_task(run_consistency_check, safe_project, str(path))

    return templates.TemplateResponse(
        "_save_result.html",
        {"request": request, "saved_at": utc_now().strftime("%Y-%m-%d %H:%M:%S UTC"), "word_count": new_words, "new_mtime": new_mtime},
    )








@app.get("/projects/{project}/characters", response_class=HTMLResponse)
def characters_page_legacy(request: Request, project: str) -> Response:
    """Legacy redirect; new flow uses /projects/{project}/stage/characters."""
    return RedirectResponse(url=f"/projects/{project}/stage/characters", status_code=301)


@app.get("/projects/{project}/world", response_class=HTMLResponse)
def world_page_legacy(request: Request, project: str) -> Response:
    """Legacy redirect; new flow uses /projects/{project}/stage/worldview."""
    return RedirectResponse(url=f"/projects/{project}/stage/worldview", status_code=301)


@app.get("/projects/{project}/notes/{folder}/{filename}", response_class=HTMLResponse)
def note_preview(request: Request, project: str, folder: str, filename: str) -> Response:
    safe_project = safe_slug(project, fallback="project")
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    if folder not in {"characters", "world"}:
        raise HTTPException(status_code=400, detail="invalid folder")
    safe_project = safe_slug(project, fallback="project")
    p = project_path(safe_project) / folder / (safe_slug(filename.replace(".md", "")) + ".md")
    p = _ensure_under_root(p, VAULT_ROOT)
    if not p.exists():
        raise HTTPException(status_code=404, detail="not found")
    _, body = read_markdown(p)
    ext = WikiLinkExtension(project=safe_project, db_path=str(DB_PATH))
    html = markdown.markdown(body, extensions=["fenced_code", "tables", ext])
    return templates.TemplateResponse("_note_preview.html", {"request": request, "title": p.stem, "html": html})

@app.delete("/projects/{project}/notes/{folder}/{filename}")
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
    return JSONResponse(content={"status": "ok", "new_url": f"/projects/{safe_project}/{folder}"})

@app.put("/projects/{project}/notes/{folder}/{filename}/rename")
async def rename_note(request: Request, project: str, folder: str, filename: str) -> Response:
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
            conn.execute("UPDATE file_index SET path=? WHERE path=?", (str(new_p), str(old_p)))
        log_operation("rename_note", f"{old_p.name} -> {new_p.name}")
        
    return JSONResponse(content={"status": "ok", "new_url": f"/projects/{safe_project}/{folder}"})




@app.get("/api/projects/{project}/export")
def api_export(request: Request, project: str, 
               format: str = "epub", volume: str = None,
               from_chapter: int = 0, to_chapter: int = 999999,
               status: str = None) -> Response:
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
        
        proj_meta = conn.execute("SELECT * FROM project_meta WHERE project=?", (safe_project,)).fetchone()
        vol_meta = None
        if volume:
            vol_meta = conn.execute(
                "SELECT * FROM volumes WHERE project=? AND slug=?", (safe_project, volume)
            ).fetchone()
    
    title = (vol_meta["title"] if vol_meta else None) or safe_project
    author = (proj_meta["author"] if proj_meta else "") or "Anonymous"
    
    if format == "txt":
        out = []
        for ch in chapters:
            _, body = read_markdown(Path(ch["path"]))
            import re as _re
            body = _re.sub(r"\[\[(?:ent_[a-z0-9]+\|)?([^\[\]|#]+?)(?:#[^\[\]]*)?\]\]", r"\1", body)
            out.append(f"# {ch['title']}\n\n{body}")
        text = "\n\n---\n\n".join(out)
        return Response(content=text.encode("utf-8"),
                        media_type="text/plain",
                        headers={"Content-Disposition": f'attachment; filename="{title}.txt"'})
    
    if format == "md":
        out = []
        for ch in chapters:
            _, body = read_markdown(Path(ch["path"]))
            out.append(f"# {ch['title']}\n\n{body}")
        text = "\n\n---\n\n".join(out)
        return Response(content=text.encode("utf-8"),
                        media_type="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="{title}.md"'})
    
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
        body = _re.sub(r"\[\[(?:ent_[a-z0-9]+\|)?([^\[\]|#]+?)(?:#[^\[\]]*)?\]\]", r"\1", body)
        html = markdown.markdown(body)
        c = epub.EpubHtml(title=ch["title"], file_name=f"chap_{ch['chapter_int']:05d}.xhtml", lang="zh")
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
    
    return Response(content=buf.read(),
                    media_type="application/epub+zip",
                    headers={"Content-Disposition": f'attachment; filename="{title}.epub"'})

@app.get("/projects/{project}/export/all")
def export_project_combined_redirect(request: Request, project: str):
    return RedirectResponse(url=f"/api/projects/{project}/export?format=md", status_code=303)


@app.get("/projects/{project}/backups/{filename}")
def list_backups(request: Request, project: str, filename: str):
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    p = chapter_path(safe_project, filename)
    rel = p.relative_to(VAULT_ROOT)
    dest_dir = BACKUP_ROOT / rel.parent
    
    backups = []
    if dest_dir.exists():
        for b in sorted(dest_dir.glob(f"*__{filename}"), key=lambda x: x.name, reverse=True):
            timestamp = b.name.split("__")[0]
            backups.append({"name": b.name, "timestamp": timestamp, "size": b.stat().st_size})
            
    return JSONResponse(content={"backups": backups})


@app.get("/projects/{project}/diff/{backup_name}")
def view_diff(request: Request, project: str, backup_name: str):
    require_auth(request)
    # Extract original filename from backup_name (format: YYYYMMDDTHHMMSSZ__filename.md)
    if "__" not in backup_name:
        raise HTTPException(400)
    
    filename = backup_name.split("__", 1)[1]
    safe_project = safe_slug(project, fallback="project")
    current_p = chapter_path(safe_project, filename)
    
    # Locate backup file
    rel = current_p.relative_to(VAULT_ROOT)
    backup_p = BACKUP_ROOT / rel.parent / backup_name
    
    if not current_p.exists() or not backup_p.exists():
        raise HTTPException(404)
        
    import difflib
    current_text = current_p.read_text(encoding="utf-8").splitlines()
    backup_text = backup_p.read_text(encoding="utf-8").splitlines()
    
    diff = list(difflib.unified_diff(backup_text, current_text, fromfile="备份", tofile="当前"))
    return templates.TemplateResponse("_diff.html", {"request": request, "diff": diff})


@app.post("/api/snapshots/{snap_id}/restore")
def restore_snapshot(request: Request, snap_id: int) -> Response:
    require_auth(request)
    with get_conn() as conn:
        snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snap_id,)).fetchone()
        if not snap: raise HTTPException(404, "Snapshot not found")
        
        content = gzip.decompress(snap["content"]).decode("utf-8")
        path = Path(snap["chapter_path"])
        
        # Backup current state as snapshot before overwriting
        backup_file(path, label="pre-restore")
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
            
        # Re-index
        fm, body = read_markdown(path)
        write_markdown(path, fm, body)
        
        return JSONResponse(content={"status": "ok"})


@app.post("/projects/{project}/backups/{backup_name}/restore")
def restore_backup(request: Request, project: str, backup_name: str):
    require_auth(request)
    if "__" not in backup_name:
        raise HTTPException(400)
    filename = backup_name.split("__", 1)[1]
    safe_project = safe_slug(project, fallback="project")
    current_p = chapter_path(safe_project, filename)
    rel = current_p.relative_to(VAULT_ROOT)
    backup_p = BACKUP_ROOT / rel.parent / backup_name
    if not backup_p.exists():
        raise HTTPException(404)
    # Backup current before restoring
    backup_file(current_p)
    import shutil
    shutil.copy2(backup_p, current_p)
    # Reload and index
    fm, body = read_markdown(current_p)
    write_markdown(current_p, fm, body)
    log_operation("restore_backup", str(current_p), backup_name, project=safe_project)
    return JSONResponse(content={"status": "ok"})




# --- C-Route (v6) API Routes ---
@app.post("/api/chapters/snapshot")
async def manual_snapshot(request: Request) -> Response:
    require_auth(request)
    data = await request.json()
    project = data.get("project")
    filename = data.get("filename")
    label = data.get("label", "manual")
    
    safe_project = safe_slug(project)
    path = chapter_path(safe_project, filename)
    if not path.exists(): raise HTTPException(404)
    
    content = path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    compressed = gzip.compress(content.encode("utf-8"))
    
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO snapshots(chapter_path, created_at, label, content_hash, content, protected) VALUES (?, ?, ?, ?, ?, ?)",
            (str(path), utc_now().isoformat(), label, content_hash, compressed, 1)
        )
    return JSONResponse({"status": "ok"})

@app.get("/projects/{project}/snapshots/{snap_id}/diff")
def view_snapshot_diff(request: Request, project: str, snap_id: int) -> Response:
    require_auth(request)
    with get_conn() as conn:
        snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snap_id,)).fetchone()
        if not snap: raise HTTPException(404)
        
    path = Path(snap["chapter_path"])
    current_text = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    backup_text = gzip.decompress(snap["content"]).decode("utf-8").splitlines()
    
    import difflib
    diff = list(difflib.unified_diff(backup_text, current_text, fromfile=f"Snapshot ({snap['label']})", tofile="Current"))
    return templates.TemplateResponse("_diff.html", {"request": request, "diff": diff, "snap_id": snap_id})

async def run_consistency_check(project: str, chapter_path: str):
    """Background task to check consistency for a chapter."""
    try:
        api_key = get_setting_decrypted("ai_api_key")
        base_url = get_setting("ai_base_url")
        model = get_setting("ai_model")
        if not api_key: return

        path = Path(chapter_path)
        if not path.exists(): return
        fm, body = read_markdown(path)

        # Get context
        from app.services.ai_context import build_context
        context = build_context(project, chapter_path, "check")

        prompt = f"""Context:
{context}

Chapter text to check:
{body}

Please list any plot inconsistencies, out-of-character behaviors, or timeline errors you find. Return as a JSON list of strings. If none, return [].
"""
        from app.services.ai_client import generate_ai_content
        response = await generate_ai_content(api_key, base_url, model, "You are a consistency checker. Return ONLY valid JSON array.", prompt)
        
        if response:
            # Try to parse JSON
            import json
            try:
                # Basic cleanup
                clean = response.strip()
                if clean.startswith("```json"): clean = clean[7:]
                if clean.endswith("```"): clean = clean[:-3]
                issues = json.loads(clean.strip())
                if isinstance(issues, list):
                    with get_conn() as conn:
                        conn.execute(
                            """INSERT INTO consistency_reports (chapter_path, created_at, issues)
                               VALUES (?, ?, ?)
                               ON CONFLICT(chapter_path) DO UPDATE SET created_at=excluded.created_at, issues=excluded.issues""",
                            (chapter_path, utc_now().isoformat(), json.dumps(issues))
                        )
            except Exception as e:
                print(f"Failed to parse consistency JSON: {e}")
    except Exception as e:
        print(f"Consistency check error: {e}")

@app.post("/api/projects/{project}/ai/outline/volume")
async def ai_outline_volume(request: Request, project: str) -> Response:
    """T4.3 AI generate volume outline."""
    require_feature("ai")
    require_auth(request)
    data = await request.json()
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key: raise HTTPException(400, "AI not configured")
    
    from app.services.ai_context import build_context
    from app.services.ai_client import generate_ai_content
    context = build_context(project, None, "outline")
    base_prompt = f"基于以下项目上下文:\n{context}\n\n请为名为 '{data.get('slug')}' 的卷生成 3-5 段中文大纲。直接返回大纲正文,不要前后说明。"
    from app.services.prompts_service import build_layered_prompt
    user_prompt = build_layered_prompt(get_setting, project, "outline", base_prompt)

    resp = await generate_ai_content(api_key, get_setting("ai_base_url"), get_setting("ai_model"), "你是一名资深小说大纲规划师,中文输出。", user_prompt)
    if resp:
        with get_conn() as conn:
            conn.execute("UPDATE volumes SET synopsis = ? WHERE project = ? AND slug = ?", (resp.strip(), project, data.get("slug")))
        return JSONResponse({"status": "ok", "synopsis": resp.strip()})
    return JSONResponse({"status": "error"})

@app.post("/api/projects/{project}/ai/outline/chapter")
async def ai_outline_chapter(request: Request, project: str) -> Response:
    """T4.3: AI splits a volume into N chapter outlines."""
    require_feature("ai")
    require_auth(request)
    data = await request.json()
    volume_slug = data.get("slug")
    num_chapters = int(data.get("count", 10))
    
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key: raise HTTPException(400, "AI not configured")
    
    safe_project = safe_slug(project, fallback="project")
    from app.services.ai_context import build_context
    from app.services.ai_client import generate_ai_content
    
    context = build_context(safe_project, None, "outline")
    
    with get_conn() as conn:
        vol = conn.execute("SELECT * FROM volumes WHERE project=? AND slug=?", (safe_project, volume_slug)).fetchone()
        if not vol: raise HTTPException(404, "volume not found")
    
    base_prompt = f"""卷大纲:
{vol['synopsis']}

项目上下文:
{context}

请为该卷生成 {num_chapters} 个章节大纲。返回 JSON 数组,每项包含:title(字符串,中文),synopsis(字符串,2-3 句中文)。只返回合法 JSON,不要 markdown 代码块,不要任何前后说明。"""
    from app.services.prompts_service import build_layered_prompt
    user_prompt = build_layered_prompt(get_setting, safe_project, "chapter_outline", base_prompt)
    response = await generate_ai_content(api_key, get_setting("ai_base_url"), get_setting("ai_model"), "你是一名章节大纲生成器。只输出合法 JSON。", user_prompt)
    
    if not response: return JSONResponse({"status": "error"})
    
    clean = response.strip()
    if clean.startswith("```json"): clean = clean[7:]
    if clean.startswith("```"): clean = clean[3:]
    if clean.endswith("```"): clean = clean[:-3]
    
    try:
        import json
        chapters_data = json.loads(clean.strip())
    except json.JSONDecodeError:
        return JSONResponse({"status": "error", "detail": "AI returned invalid JSON"})
    
    # Find current max chapter_int in this volume
    with get_conn() as conn:
        max_ch = conn.execute(
            "SELECT MAX(chapter_int) as m FROM file_index WHERE project=? AND volume=?",
            (safe_project, volume_slug)
        ).fetchone()
        start_idx = (max_ch["m"] or 0) + 1
    
    created = []
    for i, ch in enumerate(chapters_data):
        idx = start_idx + i
        title = ch.get("title", f"第 {idx} 章")
        synopsis = ch.get("synopsis", "")
        filename = f"{idx:05d}-{safe_slug(title, fallback=f'ch{idx}')}.md"
        
        new_path = NOVELS_ROOT / safe_project / "chapters" / volume_slug / filename
        new_path.parent.mkdir(parents=True, exist_ok=True)
        
        if new_path.exists(): continue  # don't overwrite
        
        fm = {
            "title": title,
            "chapter": str(idx),
            "status": "outline",
            "volume": volume_slug,
            "synopsis": synopsis,
        }
        write_markdown(new_path, fm, "")
        created.append({"filename": filename, "title": title})
    
    return JSONResponse({"status": "ok", "created": created})

@app.post("/api/projects/{project}/ai/outline/scene")
async def ai_outline_scene(request: Request, project: str) -> Response:
    """Split a chapter into scene H2 markers with summaries."""
    require_feature("ai")
    require_feature("scenes")
    require_auth(request)
    data = await request.json()
    chapter_filename = data.get("chapter")
    num_scenes = int(data.get("count", 4))
    
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key: raise HTTPException(400, "AI not configured")
    
    safe_project = safe_slug(project, fallback="project")
    ch_path = chapter_path(safe_project, chapter_filename)
    if not ch_path.exists(): raise HTTPException(404, "chapter not found")
    
    fm, body = read_markdown(ch_path)
    
    from app.services.ai_context import build_context
    from app.services.ai_client import generate_ai_content
    context = build_context(safe_project, str(ch_path), "outline")
    
    prompt = f"""Chapter: {fm.get('title', '')}
Synopsis: {fm.get('synopsis', '')}

{context}

Generate {num_scenes} scenes for this chapter. Each scene should be a beat in the chapter. Return ONLY valid JSON array, each item: {{"title": "scene title", "summary": "2 sentence beat description"}}. No markdown fences."""
    
    response = await generate_ai_content(
        api_key, get_setting("ai_base_url"), get_setting("ai_model"),
        "You are a scene outliner. Return ONLY valid JSON.", prompt
    )
    if not response: return JSONResponse({"status": "error"})
    
    clean = response.strip()
    if clean.startswith("```json"): clean = clean[7:]
    if clean.startswith("```"): clean = clean[3:]
    if clean.endswith("```"): clean = clean[:-3]
    
    try:
        import json
        scenes_data = json.loads(clean.strip())
    except json.JSONDecodeError:
        return JSONResponse({"status": "error", "detail": "AI returned invalid JSON"})
    
    # Build new body: keep original prefix until first H2 (or original body if no H2),
    # then append H2 + summary blocks for each scene
    h2_pos = body.find("\n## ")
    prefix = body[:h2_pos] if h2_pos > 0 else body
    if prefix and not prefix.endswith("\n"): prefix += "\n"
    
    new_body = prefix
    for sc in scenes_data:
        new_body += f"\n## {sc.get('title', 'Scene')}\n\n*{sc.get('summary', '')}*\n\n"
    
    write_markdown(ch_path, fm, new_body, project=safe_project)
    return JSONResponse({"status": "ok", "scenes": scenes_data})

@app.post("/api/projects/{project}/ai/outline/draft")
async def ai_outline_draft(request: Request, project: str) -> Response:
    """Expand a scene's summary into prose draft."""
    require_feature("ai")
    require_feature("scenes")
    require_auth(request)
    data = await request.json()
    chapter_filename = data.get("chapter")
    scene_id = data.get("scene_id")
    
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key: raise HTTPException(400, "AI not configured")
    
    safe_project = safe_slug(project, fallback="project")
    ch_path = chapter_path(safe_project, chapter_filename)
    if not ch_path.exists(): raise HTTPException(404, "chapter not found")
    
    with get_conn() as conn:
        scene = conn.execute(
            "SELECT * FROM scenes WHERE id=? AND project=?",
            (scene_id, safe_project)
        ).fetchone()
        if not scene: raise HTTPException(404, "scene not found")
    
    fm, body = read_markdown(ch_path)
    
    # Extract scene segment by char offsets
    start = scene["char_offset_start"] or 0
    end = scene["char_offset_end"] or len(body)
    scene_text = body[start:end]
    
    from app.services.ai_context import build_context
    from app.services.ai_client import generate_ai_content
    context = build_context(safe_project, str(ch_path), "draft")
    
    prompt = f"""{context}

Current scene to expand:
{scene_text}

Expand this scene into prose. Keep the existing H2 title and beat summary at top, but write 500-1000 words of actual narrative below. Match the project's style. Return only the expanded scene text (markdown OK)."""
    
    response = await generate_ai_content(
        api_key, get_setting("ai_base_url"), get_setting("ai_model"),
        "You are a creative novelist.", prompt
    )
    if not response: return JSONResponse({"status": "error"})
    
    new_body = body[:start] + response.strip() + "\n\n" + body[end:]
    write_markdown(ch_path, fm, new_body, project=safe_project)
    return JSONResponse({"status": "ok", "draft": response.strip()})

@app.get("/api/projects/{project}/ai/generate")
async def api_ai_generate(
    request: Request, 
    project: str, 
    mode: str = "continue",
    chapter: str = None,
    text: str = ""
) -> Response:
    require_feature("ai")
    require_auth(request)
    
    # Get settings
    api_key = get_setting_decrypted("ai_api_key")
    base_url = get_setting("ai_base_url")
    model = get_setting("ai_model")
    
    if not api_key:
        return JSONResponse({"status": "error", "detail": "AI API Key not set"}, status_code=400)

    # Build Context
    safe_project = safe_slug(project)
    ch_path = None
    if chapter:
        ch_path = str(chapter_path(safe_project, chapter))
    
    full_context = build_context(safe_project, ch_path, mode)
    
    system_prompt = "你是一名资深小说写作助手。中文输出。保持与上下文一致的人物性格、世界观、文风。"
    mode_instructions = {
        "continue": "请基于以下上下文,自然地续写接下来的几段。",
        "rewrite": "请润色用户给出的文本,保持原意,提升语感与节奏。",
        "check": "请检查用户给出的文本,找出与项目设定不一致或前后矛盾之处。直接列出。",
        "echo": "请基于上下文,建议本章如何呼应之前埋下的伏笔或设定。",
    }
    base_prompt = f"""项目上下文:
{full_context}

任务:{mode_instructions.get(mode, mode_instructions['continue'])}

用户提供的文本:
{text or '(无)'}
"""
    from app.services.prompts_service import build_layered_prompt
    user_prompt = build_layered_prompt(get_setting, safe_project, "writing", base_prompt)

    async def event_generator():
        async for chunk in generate_ai_content_stream(api_key, base_url, model, system_prompt, user_prompt):
            yield f"data: {json.dumps({'content': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    from fastapi.responses import StreamingResponse
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/projects/{project}/outline", response_class=HTMLResponse)
def project_outline_page_legacy(request: Request, project: str) -> Response:
    """Legacy redirect; new flow uses /projects/{project}/stage/outline."""
    return RedirectResponse(url=f"/projects/{project}/stage/outline", status_code=301)

@app.put("/api/projects/{project}/volumes/{slug}")
async def api_update_volume(request: Request, project: str, slug: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT 1 FROM volumes WHERE project=? AND slug=?",
            (safe_project, slug)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE volumes SET title=?, synopsis=?, target_words=?, seq=?
                   WHERE project=? AND slug=?""",
                (data.get("title", slug), data.get("synopsis", ""),
                 int(data.get("target_words") or 0), int(data.get("seq") or 0),
                 safe_project, slug)
            )
        else:
            conn.execute(
                """INSERT INTO volumes(project, slug, title, synopsis, target_words, seq)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (safe_project, slug, data.get("title", slug), data.get("synopsis", ""),
                 int(data.get("target_words") or 0), int(data.get("seq") or 0))
            )
    return JSONResponse({"status": "ok"})

@app.post("/api/projects/{project}/volumes/reorder")
async def api_reorder_volumes(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    slugs = data.get("order", [])
    with get_conn() as conn:
        for i, slug in enumerate(slugs, 1):
            conn.execute(
                "UPDATE volumes SET seq=? WHERE project=? AND slug=?",
                (i, safe_project, slug)
            )
    return JSONResponse({"status": "ok"})

@app.get("/api/projects/{project}/volumes")
def api_list_volumes(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    chapters_dir = NOVELS_ROOT / safe_project / "chapters"
    if chapters_dir.exists():
        with get_conn() as conn:
            existing = {r["slug"] for r in conn.execute(
                "SELECT slug FROM volumes WHERE project=?", (safe_project,)).fetchall()}
            for d in sorted(chapters_dir.iterdir()):
                if d.is_dir() and d.name not in existing:
                    conn.execute(
                        "INSERT INTO volumes(project, slug, title, seq) VALUES (?, ?, ?, ?)",
                        (safe_project, d.name, d.name, len(existing) + 1)
                    )
                    existing.add(d.name)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT v.*, 
               (SELECT COUNT(*) FROM file_index WHERE project=v.project AND volume=v.slug) as chapter_count,
               (SELECT COALESCE(SUM(word_count),0) FROM file_index WHERE project=v.project AND volume=v.slug) as word_count
               FROM volumes v WHERE v.project=? ORDER BY v.seq""",
            (safe_project,)
        ).fetchall()
    return JSONResponse({"status": "ok", "volumes": [dict(r) for r in rows]})

@app.get("/api/entities/{ent_id}/appearances")
def api_get_entity_appearances(request: Request, ent_id: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        appearances = conn.execute("""
            SELECT er.*, fi.title as chapter_title 
            FROM entity_refs er
            LEFT JOIN file_index fi ON er.chapter_path = fi.path
            WHERE er.entity_id = ?
        """, (ent_id,)).fetchall()
        return JSONResponse(content={"status": "ok", "appearances": [dict(a) for a in appearances]})


@app.get("/api/entities")
def api_list_entities(request: Request, project: str, kind: str = None, q: str = None) -> Response:
    require_auth(request)
    with get_conn() as conn:
        query = "SELECT * FROM entities WHERE project = ?"
        params = [project]
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        if q:
            query += " AND (name LIKE ? OR aliases LIKE ?)"
            params.append(f"%{q}%")
            params.append(f"%{q}%")
        rows = conn.execute(query, params).fetchall()
        return JSONResponse(content={"status": "ok", "entities": [dict(r) for r in rows]})

@app.post("/api/entities")
async def api_create_entity(request: Request) -> Response:
    require_auth(request)
    data = await request.json()
    project = data.get("project")
    kind = data.get("kind")
    name = data.get("name")
    if not project or not name:
        raise HTTPException(400, "project and name required")
    
    ent_id = data.get("id") or f"ent_{hashlib.sha1((project + name + str(utc_now())).encode()).hexdigest()[:8]}"
    now = utc_now().isoformat()
    
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO entities (id, project, kind, name, aliases, properties, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ent_id, project, kind, name, json.dumps(data.get("aliases", [])), json.dumps(data.get("properties", {})), now, now)
        )
        conn.execute(
            "INSERT INTO entity_fts (id, project, name, aliases, properties) VALUES (?, ?, ?, ?, ?)",
            (ent_id, project, name, json.dumps(data.get("aliases", [])), json.dumps(data.get("properties", {})))
        )
    return JSONResponse(content={"status": "ok", "id": ent_id})

@app.put("/api/entities/{ent_id}")
async def api_update_entity(request: Request, ent_id: str) -> Response:
    require_auth(request)
    data = await request.json()
    with get_conn() as conn:
        old = conn.execute("SELECT * FROM entities WHERE id=?", (ent_id,)).fetchone()
        if not old: raise HTTPException(404)
        
        new_name = data["name"]
        new_aliases = json.dumps(data.get("aliases", []))
        new_props = json.dumps(data.get("properties", {}))
        now = utc_now().isoformat()
        
        # Diff & log property changes
        try:
            old_props = json.loads(old["properties"] or "{}")
        except Exception:
            old_props = {}
        new_props_dict = data.get("properties", {})
        for k in set(old_props.keys()) | set(new_props_dict.keys()):
            if old_props.get(k) != new_props_dict.get(k):
                conn.execute(
                    """INSERT INTO entity_history(entity_id, project, chapter_int, field, old_value, new_value, changed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (ent_id, old["project"], None, k,
                     json.dumps(old_props.get(k)) if old_props.get(k) is not None else None,
                     json.dumps(new_props_dict.get(k)) if new_props_dict.get(k) is not None else None,
                     now)
                )
        if old["name"] != new_name:
            conn.execute(
                """INSERT INTO entity_history(entity_id, project, chapter_int, field, old_value, new_value, changed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ent_id, old["project"], None, "name", old["name"], new_name, now)
            )
        
        conn.execute(
            "UPDATE entities SET name=?, aliases=?, properties=?, updated_at=? WHERE id=?",
            (new_name, new_aliases, new_props, now, ent_id)
        )
        conn.execute("DELETE FROM entity_fts WHERE id=?", (ent_id,))
        conn.execute(
            "INSERT INTO entity_fts (id, project, name, aliases, properties) VALUES (?, ?, ?, ?, ?)",
            (ent_id, old["project"], new_name, new_aliases, new_props)
        )
        
        # Save markdown body to disk if md_path exists
        md_content = data.get("md_content")
        if md_content is not None and old["md_path"]:
            md_path = Path(old["md_path"])
            if md_path.exists():
                fm, _ = read_markdown(md_path)
                fm["title"] = new_name  # update title in frontmatter too
                write_markdown(md_path, fm, md_content, project=old["project"])
        
        # Cascade rename (rewrite display_text in [[ent_xxx|old_name]])
        if data.get("cascade") and old["name"] != new_name:
            chapters_dir = NOVELS_ROOT / old["project"] / "chapters"
            import re as _re
            pattern = _re.compile(rf"\[\[{_re.escape(ent_id)}\|[^\[\]]*?\]\]")
            for f in chapters_dir.rglob("*.md") if chapters_dir.exists() else []:
                try:
                    text = f.read_text(encoding="utf-8")
                    new_text = pattern.sub(f"[[{ent_id}|{new_name}]]", text)
                    if new_text != text:
                        f.write_text(new_text, encoding="utf-8")
                except Exception:
                    pass
    
    return JSONResponse(content={"status": "ok"})

@app.delete("/api/entities/{ent_id}")
def api_delete_entity(request: Request, ent_id: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        conn.execute("DELETE FROM entities WHERE id=?", (ent_id,))
        conn.execute("DELETE FROM entity_fts WHERE id=?", (ent_id,))
        conn.execute("DELETE FROM entity_relations WHERE source_id=? OR target_id=?", (ent_id, ent_id))
        conn.execute("DELETE FROM entity_refs WHERE entity_id=?", (ent_id,))
    return JSONResponse(content={"status": "ok"})

@app.get("/api/entities/{ent_id}")
def api_get_entity(request: Request, ent_id: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        entity = conn.execute("SELECT * FROM entities WHERE id = ?", (ent_id,)).fetchone()
        if not entity: raise HTTPException(404)
        relations = conn.execute(
            """SELECT er.*, e.name as target_name FROM entity_relations er 
               JOIN entities e ON er.target_id = e.id 
               WHERE source_id = ?""", (ent_id,)).fetchall()
        appearances = conn.execute("SELECT * FROM entity_refs WHERE entity_id = ?", (ent_id,)).fetchall()
        return JSONResponse(content={
            "status": "ok", 
            "entity": dict(entity), 
            "relations": [dict(r) for r in relations],
            "appearances": [dict(a) for a in appearances]
        })

@app.post("/api/entity-relations")
async def api_create_relation(request: Request) -> Response:
    require_auth(request)
    data = await request.json()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO entity_relations (project, source_id, target_id, relation_type, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (data["project"], data["source_id"], data["target_id"], data["relation_type"], data.get("notes", ""), utc_now().isoformat())
        )
    return JSONResponse(content={"status": "ok"})


@app.delete("/api/entity-relations/{rel_id}")
def api_delete_relation(request: Request, rel_id: int) -> Response:
    require_auth(request)
    with get_conn() as conn:
        conn.execute("DELETE FROM entity_relations WHERE id = ?", (rel_id,))
    return JSONResponse(content={"status": "ok"})


@app.post("/api/projects/{project}/snapshots")
async def api_create_snapshot(request: Request, project: str) -> Response:
    require_auth(request)
    data = await request.json()
    path_str = data.get("path")
    label = data.get("label", "manual")
    if not path_str: raise HTTPException(400, "path required")
    
    path = Path(path_str)
    if not path.exists(): raise HTTPException(404, "file not found")
    
    backup_file(path, label=label)
    return JSONResponse(content={"status": "ok"})


@app.get("/api/projects/{project}/scenes")
def api_list_scenes(request: Request, project: str, chapter: str = None) -> Response:
    require_feature("scenes")
    require_auth(request)
    with get_conn() as conn:
        query = "SELECT * FROM scenes WHERE project = ?"
        params = [project]
        if chapter:
            query += " AND chapter_path LIKE ?"
            params.append(f"%{chapter}")
        query += " ORDER BY chapter_path, seq"
        rows = conn.execute(query, params).fetchall()
        return JSONResponse(content={"status": "ok", "scenes": [dict(r) for r in rows]})


@app.post("/api/projects/{project}/scenes")
async def api_create_scene(request: Request, project: str) -> Response:
    require_feature("scenes")
    require_auth(request)
    data = await request.json()
    # Logic to insert H2 into file or just record in DB? 
    # Usually we want it in DB for the outline
    sc_id = f"sc_{hashlib.sha1((project + data['chapter_path'] + str(utc_now())).encode()).hexdigest()[:8]}"
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO scenes (id, chapter_path, project, seq, title, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sc_id, data["chapter_path"], project, data.get("seq", 0), data.get("title", "New Scene"), "draft")
        )
    return JSONResponse(content={"status": "ok", "id": sc_id})


@app.post("/api/projects/{project}/bulk-bind-entities")
async def api_bulk_bind_entities(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    only_for = data.get("entity_id")
    
    chapters_dir = NOVELS_ROOT / safe_project / "chapters"
    if not chapters_dir.exists():
        return JSONResponse({"status": "ok", "updated": 0})
    
    with get_conn() as conn:
        if only_for:
            ent = conn.execute(
                "SELECT id, name, aliases FROM entities WHERE id=? AND project=?",
                (only_for, safe_project)
            ).fetchone()
            if not ent: raise HTTPException(404, "entity not found")
            entities = [ent]
        else:
            entities = conn.execute(
                "SELECT id, name, aliases FROM entities WHERE project=?",
                (safe_project,)
            ).fetchall()
    
    import json
    name_map = {}
    for e in entities:
        name_map[e["name"]] = e["id"]
        try:
            aliases = json.loads(e["aliases"] or "[]")
            for a in aliases:
                name_map[a] = e["id"]
        except Exception:
            pass
    
    updated = 0
    import re as _re
    pattern = _re.compile(r"\[\[([^\[\]|#]+?)\]\]")
    
    for f in chapters_dir.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
            new_text = pattern.sub(lambda m: 
                f"[[{name_map[m.group(1).strip()]}|{m.group(1).strip()}]]" 
                if m.group(1).strip() in name_map else m.group(0),
                text
            )
            if new_text != text:
                f.write_text(new_text, encoding="utf-8")
                fm, body = read_markdown(f)
                write_markdown(f, fm, body, project=safe_project)
                updated += 1
        except Exception as e:
            print(f"bulk-bind error on {f}: {e}")
    
    return JSONResponse({"status": "ok", "updated": updated})


@app.get("/projects/{project}/entities", response_class=HTMLResponse)
def entities_page(request: Request, project: str, kind: str = None) -> Response:
    require_auth(request)
    with get_conn() as conn:
        query = "SELECT * FROM entities WHERE project = ?"
        params = [project]
        if kind:
            if kind == 'world': # legacy mapping
                query += " AND kind NOT IN ('character', 'thread')"
            else:
                query += " AND kind = ?"
                params.append(kind)
        entities = conn.execute(query, params).fetchall()
        return templates.TemplateResponse(
            "entities_list.html",
            {
                "request": request,
                "project": project,
                "entities": [dict(e) for e in entities],
                "kind": kind
            }
        )


@app.get("/api/entity-relations")
def api_list_relations(request: Request, project: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        relations = conn.execute("SELECT * FROM entity_relations WHERE project = ?", (project,)).fetchall()
        return JSONResponse(content={"status": "ok", "relations": [dict(r) for r in relations]})


@app.get("/projects/{project}/entities/{ent_id}", response_class=HTMLResponse)
def entity_detail_page(request: Request, project: str, ent_id: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        entity = conn.execute("SELECT * FROM entities WHERE id = ?", (ent_id,)).fetchone()
        if not entity: raise HTTPException(404)
        
        # Load markdown content from md_path if exists
        md_content = ""
        if entity["md_path"] and Path(entity["md_path"]).exists():
            fm, md_content = read_markdown(Path(entity["md_path"]))
            
        relations = conn.execute(
            """SELECT er.*, e.name as target_name FROM entity_relations er 
               JOIN entities e ON er.target_id = e.id 
               WHERE source_id = ?""", (ent_id,)).fetchall()
        appearances = conn.execute("SELECT * FROM entity_refs WHERE entity_id = ?", (ent_id,)).fetchall()
        try:
            entity_properties = json.loads(entity["properties"] or "{}")
        except json.JSONDecodeError:
            entity_properties = {}

        return templates.TemplateResponse(
            "entity_detail.html",
            {
                "request": request,
                "project": project,
                "entity": dict(entity),
                "entity_aliases": json.loads(entity["aliases"] or "[]"),
                "entity_properties": entity_properties,
                "md_content": md_content,
                "relations": [dict(r) for r in relations],
                "appearances": [dict(a) for a in appearances]
            }
        )


@app.put("/api/scenes/{sc_id}")
async def api_update_scene(request: Request, sc_id: str) -> Response:
    require_feature("scenes")
    require_auth(request)
    data = await request.json()
    with get_conn() as conn:
        conn.execute(
            """UPDATE scenes SET 
               title=?, pov=?, location_id=?, summary=?, status=?
               WHERE id=?""",
            (data.get("title"), data.get("pov"), data.get("location_id"), data.get("summary"), data.get("status"), sc_id)
        )
    return JSONResponse(content={"status": "ok"})

@app.delete("/api/scenes/{sc_id}")
def api_delete_scene(request: Request, sc_id: str) -> Response:
    require_feature("scenes")
    require_auth(request)
    with get_conn() as conn:
        conn.execute("DELETE FROM scenes WHERE id=?", (sc_id,))
    return JSONResponse(content={"status": "ok"})

@app.get("/api/projects/{project}/outline")
def api_get_outline(request: Request, project: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        # Simple tree: Volumes -> Chapters -> Scenes
        volumes = conn.execute("SELECT * FROM volumes WHERE project = ? ORDER BY seq", (project,)).fetchall()
        chapters = conn.execute("SELECT * FROM file_index WHERE project = ? ORDER BY volume, chapter_int", (project,)).fetchall()
        scenes = conn.execute("SELECT * FROM scenes WHERE project = ? ORDER BY chapter_path, seq", (project,)).fetchall()
        
        return JSONResponse(content={
            "status": "ok",
            "volumes": [dict(v) for v in volumes],
            "chapters": [dict(c) for c in chapters],
            "scenes": [dict(s) for s in scenes]
        })

@app.get("/export", response_class=HTMLResponse)
def export_page(request: Request) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("export.html", {"request": request, "projects": scan_projects()})


@app.post("/export/{project}", response_class=HTMLResponse)
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
    return templates.TemplateResponse("_export_result.html", {"request": request, "project": safe_project, "path": str(export_file)})



@app.get("/api/snapshots/{snap_id}/diff")
def api_snapshot_diff(request: Request, snap_id: int) -> Response:
    require_auth(request)
    import difflib
    with get_conn() as conn:
        snap = conn.execute("SELECT * FROM snapshots WHERE id=?", (snap_id,)).fetchone()
        if not snap: raise HTTPException(404)
        old_content = gzip.decompress(snap["content"]).decode("utf-8")
    path = Path(snap["chapter_path"])
    if not path.exists(): raise HTTPException(404, "current file gone")
    new_content = path.read_text(encoding="utf-8")
    
    diff = list(difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"snapshot {snap['created_at']}",
        tofile="current",
        n=3
    ))
    return JSONResponse({
        "status": "ok",
        "diff": "".join(diff),
        "old_lines": len(old_content.splitlines()),
        "new_lines": len(new_content.splitlines()),
    })

@app.put("/api/snapshots/{snap_id}")
async def api_update_snapshot(request: Request, snap_id: int) -> Response:
    """Set label or protected flag on a snapshot."""
    require_auth(request)
    data = await request.json()
    fields = []
    params = []
    if "label" in data:
        fields.append("label=?")
        params.append(data["label"])
    if "protected" in data:
        fields.append("protected=?")
        params.append(1 if data["protected"] else 0)
    if not fields: return JSONResponse({"status": "ok"})
    params.append(snap_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE snapshots SET {','.join(fields)} WHERE id=?", params)
    return JSONResponse({"status": "ok"})


# ===== Workflow prompt slots =====

@app.get("/api/prompts/{project}/{stage}")
def api_get_prompt(request: Request, project: str, stage: str) -> Response:
    require_auth(request)
    from app.services.stage_service import is_valid_stage
    from app.services.prompts_service import get_stage_prompt
    if not is_valid_stage(stage):
        raise HTTPException(400, f"unknown stage: {stage}")
    safe_project = safe_slug(project, fallback="project")
    return JSONResponse({"status": "ok", "content": get_stage_prompt(get_setting, safe_project, stage)})


@app.put("/api/prompts/{project}/{stage}")
async def api_set_prompt(request: Request, project: str, stage: str) -> Response:
    require_auth(request)
    from app.services.stage_service import is_valid_stage
    from app.services.prompts_service import set_stage_prompt
    if not is_valid_stage(stage):
        raise HTTPException(400, f"unknown stage: {stage}")
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    set_stage_prompt(set_setting, safe_project, stage, data.get("content", ""))
    log_operation("set_stage_prompt", target=f"{safe_project}:{stage}")
    return JSONResponse({"status": "ok"})


@app.put("/api/prompts/global")
async def api_set_global_prompt(request: Request) -> Response:
    require_auth(request)
    from app.services.prompts_service import set_global_prompt
    data = await request.json()
    set_global_prompt(set_setting, data.get("content", ""))
    log_operation("set_global_prompt")
    return JSONResponse({"status": "ok"})


# ===== Stage status (mark done) =====

@app.put("/api/stages/{project}/{stage}/done")
async def api_mark_stage_done(request: Request, project: str, stage: str) -> Response:
    require_auth(request)
    from app.services.stage_service import is_valid_stage
    from app.services.prompts_service import mark_stage_done
    if not is_valid_stage(stage):
        raise HTTPException(400, f"unknown stage: {stage}")
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    mark_stage_done(set_setting, safe_project, stage, bool(data.get("done", True)))
    return JSONResponse({"status": "ok"})


# ===== Workflow stage page dispatcher =====

@app.get("/projects/{project}/stage/{stage}", response_class=HTMLResponse)
def stage_page(request: Request, project: str, stage: str) -> Response:
    """Unified workflow stage entry. Each stage renders its own template."""
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    from app.services.stage_service import is_valid_stage, STAGE_LABELS
    from app.services.prompts_service import get_stage_prompt, is_stage_done
    if not is_valid_stage(stage):
        raise HTTPException(404, f"unknown stage: {stage}")

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


@app.put("/api/projects/{project}/workflow/{stage}/content")
async def api_save_stage_content(request: Request, project: str, stage: str) -> Response:
    """Save markdown content for premise/worldview stages."""
    require_auth(request)
    from app.services.stage_service import is_valid_stage
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


# ===== Stage-specific AI generation =====

@app.post("/api/projects/{project}/stage/premise/ai")
async def api_ai_premise(request: Request, project: str) -> Response:
    require_feature("ai")
    require_auth(request)
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key:
        return JSONResponse({"status": "error", "detail": "AI 未配置,请先去设置页填 API Key"}, status_code=400)

    data = await request.json()
    action = data.get("action", "discuss")
    current = (data.get("current") or "").strip()
    safe_project = safe_slug(project, fallback="project")
    base_map = {
        "discuss": "我正在为一本长篇小说做立意。这是目前草稿。请用 3-5 句话反馈:最强点、最弱点、两个可以追问的问题。\n\n当前草稿:\n" + (current or "(空)"),
        "logline": "请基于以下方向,给我 3 个一句话简介(每个 30-60 字)。每个用不同切入角度,且要有钩子。\n\n方向:\n" + (current or "(空,你自己想 3 个有趣方向)"),
        "refs": "请推荐 5 部题材或氛围接近以下立意的作品(小说/影视),每部一行,格式:作品名 — 一句话说为什么相似。\n\n立意:\n" + (current or "(空)"),
        "critique": "请挑出以下立意的 3 个薄弱点。直接说,不要夸奖。如果立意为空,请说\"目前还没东西可以挑\"。\n\n立意:\n" + (current or "(空)"),
    }
    from app.services.ai_client import generate_ai_content
    from app.services.prompts_service import build_layered_prompt
    user_prompt = build_layered_prompt(get_setting, safe_project, "premise", base_map.get(action, base_map["discuss"]))
    text = await generate_ai_content(
        api_key,
        get_setting("ai_base_url", "https://api.openai.com/v1"),
        get_setting("ai_model", "gpt-3.5-turbo"),
        "你是一名资深小说编辑,中文输出,简洁直接,不要客套。",
        user_prompt,
    )
    if not text:
        return JSONResponse({"status": "error", "detail": "AI 没有返回内容"}, status_code=502)
    return JSONResponse({"status": "ok", "text": text})


@app.post("/api/projects/{project}/stage/worldview/ai")
async def api_ai_worldview(request: Request, project: str) -> Response:
    require_feature("ai")
    require_auth(request)
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key:
        return JSONResponse({"status": "error", "detail": "AI 未配置"}, status_code=400)

    data = await request.json()
    action = data.get("action", "extend")
    current = (data.get("current") or "").strip()
    safe_project = safe_slug(project, fallback="project")
    premise_path = NOVELS_ROOT / safe_project / ".workflow" / "premise.md"
    premise = premise_path.read_text(encoding="utf-8") if premise_path.exists() else ""
    head = f"立意参考:\n{premise[:800]}\n\n当前世界观稿:\n{current or '(空)'}\n\n"
    base_map = {
        "extend": head + "请帮我把世界观骨架填补完整。建议补:时代地理、核心规则、主要势力、视觉氛围。每节 2-4 句即可,具体不空泛。",
        "inconsistency": head + "请找出 3 处设定中的潜在漏洞或自相矛盾。直接列出,不要绕弯。",
        "names": head + "请基于上述世界观,起 8 个地名或势力名。每行一个,格式:名字 — 一句话用途。",
        "timeline": head + "请列出 5-8 个对故事至关重要的时间节点。每行一个,格式:[时间] 事件 — 影响。",
    }
    from app.services.ai_client import generate_ai_content
    from app.services.prompts_service import build_layered_prompt
    user_prompt = build_layered_prompt(get_setting, safe_project, "worldview", base_map.get(action, base_map["extend"]))
    text = await generate_ai_content(
        api_key,
        get_setting("ai_base_url", "https://api.openai.com/v1"),
        get_setting("ai_model", "gpt-3.5-turbo"),
        "你是一名资深小说世界观顾问。中文输出。具体,不空泛。",
        user_prompt,
    )
    if not text:
        return JSONResponse({"status": "error", "detail": "AI 没有返回"}, status_code=502)
    return JSONResponse({"status": "ok", "text": text})


@app.post("/api/projects/{project}/stage/characters/ai")
async def api_ai_characters(request: Request, project: str) -> Response:
    require_feature("ai")
    require_auth(request)
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key:
        return JSONResponse({"status": "error", "detail": "AI 未配置"}, status_code=400)

    data = await request.json()
    action = data.get("action", "cast")
    safe_project = safe_slug(project, fallback="project")
    premise = NOVELS_ROOT / safe_project / ".workflow" / "premise.md"
    worldview = NOVELS_ROOT / safe_project / ".workflow" / "worldview.md"
    premise_text = premise.read_text(encoding="utf-8") if premise.exists() else ""
    worldview_text = worldview.read_text(encoding="utf-8") if worldview.exists() else ""
    head = f"立意:\n{premise_text[:600]}\n\n世界观:\n{worldview_text[:800]}\n\n"
    base_map = {
        "cast": head + "请基于上面的设定,给我 3 个候选主角。每个用 4-6 行写:姓名、年龄、职业、核心动机、致命缺陷、出场动作画面。",
        "antagonist": head + "请配一个反派或对手。要求:动机能站得住脚,与主角形成镜像或互补。4-6 行。",
        "relations": head + "请基于设定,提议一个 5-7 人的人物关系网,并指出 1 个最有戏剧张力的对子。",
        "flaw": head + "请给主角一个致命缺陷(不是优点的反面),要能驱动后期反转。给 3 个候选,每个 2-3 行。",
    }
    from app.services.ai_client import generate_ai_content
    from app.services.prompts_service import build_layered_prompt
    user_prompt = build_layered_prompt(get_setting, safe_project, "characters", base_map.get(action, base_map["cast"]))
    text = await generate_ai_content(
        api_key,
        get_setting("ai_base_url", "https://api.openai.com/v1"),
        get_setting("ai_model", "gpt-3.5-turbo"),
        "你是一名资深小说人物顾问。中文输出。具体,可视化,不空泛。",
        user_prompt,
    )
    if not text:
        return JSONResponse({"status": "error", "detail": "AI 没有返回"}, status_code=502)
    return JSONResponse({"status": "ok", "text": text})


@app.put("/api/projects/{project}/chapters/{filename}/synopsis")
async def api_update_chapter_synopsis(request: Request, project: str, filename: str) -> Response:
    """Lightweight synopsis update that only touches chapter frontmatter."""
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    path = chapter_path(safe_project, filename)
    if not path.exists():
        raise HTTPException(404, "chapter not found")
    data = await request.json()
    fm, body = read_markdown(path)
    fm["synopsis"] = data.get("synopsis", "")
    write_markdown(path, fm, body, project=safe_project)
    log_operation("update_synopsis", target=str(path))
    return JSONResponse({"status": "ok"})


@app.post("/api/projects/{project}/stage/chapter_outline/ai")
async def api_ai_chapter_outline_one(request: Request, project: str) -> Response:
    """Generate a synopsis for a single chapter."""
    require_feature("ai")
    require_auth(request)
    api_key = get_setting_decrypted("ai_api_key")
    if not api_key:
        return JSONResponse({"status": "error", "detail": "AI 未配置"}, status_code=400)

    data = await request.json()
    filename = data.get("filename")
    if not filename:
        raise HTTPException(400, "missing filename")
    safe_project = safe_slug(project, fallback="project")
    path = chapter_path(safe_project, filename)
    if not path.exists():
        raise HTTPException(404)

    fm, body = read_markdown(path)
    chapters = list_chapters(safe_project, sync=False)
    idx = next((i for i, c in enumerate(chapters) if c["filename"] == filename), -1)
    prev_syn = chapters[idx - 1].get("synopsis", "") if idx > 0 else ""
    next_syn = chapters[idx + 1].get("synopsis", "") if idx >= 0 and idx + 1 < len(chapters) else ""
    base_prompt = f"""请为这一章生成 1-3 句中文梗概,用于做细纲。直接返回梗概,不要前后说明。
本章号:{fm.get('chapter', '')}
本章标题:{fm.get('title', '')}
所在卷:{fm.get('volume', '')}
上一章梗概:{prev_syn or '(无)'}
下一章梗概:{next_syn or '(无)'}
本章已写正文(摘要):{(body or '')[:500]}
"""
    from app.services.ai_client import generate_ai_content
    from app.services.prompts_service import build_layered_prompt
    user_prompt = build_layered_prompt(get_setting, safe_project, "chapter_outline", base_prompt)
    text = await generate_ai_content(
        api_key,
        get_setting("ai_base_url", "https://api.openai.com/v1"),
        get_setting("ai_model", "gpt-3.5-turbo"),
        "你是一名章节大纲规划师。中文输出。1-3 句话,具体不空泛。",
        user_prompt,
    )
    if not text:
        return JSONResponse({"status": "error", "detail": "AI 没有返回"}, status_code=502)
    return JSONResponse({"status": "ok", "text": text.strip()})
