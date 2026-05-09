from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.config import PROJECT_GOAL_WORDS, feature_enabled
from app.db import get_conn, get_setting
from app.deps import get_templates
from app.security import require_auth
from app.services.chapter_service import normalize_meta, write_markdown
from app.services.consistency_service import run_consistency_check
from app.services.library_service import list_chapters
from app.services.markdown_service import (
    count_words,
    parse_csv,
    read_markdown,
    safe_slug,
    utc_now,
)
from app.services.metrics_service import log_operation
from app.services.path_service import chapter_path
from app.services.project_service import get_project_meta
from app.services.prompts_service import get_stage_prompt
from app.services.snapshot_service import backup_file

router = APIRouter()


@router.get("/projects/{project}/editor/{filename}", response_class=HTMLResponse)
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

    active_idx = next(
        (i for i, c in enumerate(chapters) if c["filename"] == path.name), 0
    )
    start_idx = max(0, active_idx - 20)
    end_idx = min(len(chapters), active_idx + 21)
    visible_chapters = chapters[start_idx:end_idx]

    proj_meta = get_project_meta(safe_project)

    # T2.7 Sidebar Data
    with get_conn() as conn:
        mentioned = conn.execute(
            """SELECT DISTINCT e.* FROM entities e
               JOIN entity_refs er ON e.id = er.entity_id
               WHERE er.chapter_path = ?""",
            (str(path),),
        ).fetchall()

        snapshots = conn.execute(
            "SELECT id, created_at, label FROM snapshots WHERE chapter_path = ? ORDER BY created_at DESC LIMIT 50",
            (str(path),),
        ).fetchall()

    writing_prompt = get_stage_prompt(get_setting, safe_project, "writing")
    templates = get_templates()
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
            "writing_prompt": writing_prompt,
        },
    )


@router.post("/projects/{project}/editor/{filename}", response_class=HTMLResponse)
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
            loaded_mtime_val = float(loaded_mtime)
            current_mtime = path.stat().st_mtime
            if abs(current_mtime - loaded_mtime_val) > 0.5 and not force:
                current_content = path.read_text(encoding="utf-8") if path.exists() else ""
                templates = get_templates()
                return templates.TemplateResponse(
                    "_save_result.html",
                    {
                        "request": request,
                        "error": "文件已被其他来源修改，请先查看冲突再决定是否覆盖。",
                        "error_code": "chapter_conflict",
                        "current_mtime": current_mtime,
                        "loaded_mtime": loaded_mtime_val,
                        "current_hash": hashlib.sha256(
                            current_content.encode("utf-8")
                        ).hexdigest(),
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
            conn.execute(
                "UPDATE file_index SET path=? WHERE path=?",
                (str(expected_path), str(path)),
            )
            conn.execute(
                "UPDATE chapter_fts SET path=? WHERE path=?",
                (str(expected_path), str(path)),
            )
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
        words_added = 0  # Don't record negative progress in trend

    if force and path.exists():
        backup_file(path, label="pre_overwrite")
    write_markdown(path, frontmatter, body)
    log_operation(
        "save",
        str(path),
        f"words_added={words_added}",
        value=words_added,
        project=safe_project,
    )
    new_mtime = path.stat().st_mtime
    if feature_enabled("ai_check"):
        background_tasks.add_task(run_consistency_check, safe_project, str(path))

    templates = get_templates()
    return templates.TemplateResponse(
        "_save_result.html",
        {
            "request": request,
            "saved_at": utc_now().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "word_count": new_words,
            "new_mtime": new_mtime,
        },
    )
