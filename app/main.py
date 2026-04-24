from __future__ import annotations

import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
VAULT_ROOT = Path(os.getenv("NOVELHUB_VAULT_ROOT", "/root/ObsidianVault"))
NOVELS_ROOT = VAULT_ROOT / "Novels"
BACKUP_ROOT = Path(os.getenv("NOVELHUB_BACKUP_ROOT", str(VAULT_ROOT / ".novelhub-backups")))
DB_PATH = Path(os.getenv("NOVELHUB_DB_PATH", str(BASE_DIR / "novelhub.db")))
ADMIN_PASSWORD = os.getenv("NOVELHUB_PASSWORD", "")
SECRET_KEY = os.getenv("NOVELHUB_SECRET_KEY", "change-me")

app = FastAPI(title="Novel Hub")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS file_index (
                path TEXT PRIMARY KEY,
                project TEXT,
                word_count INTEGER,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                target TEXT,
                created_at TEXT NOT NULL,
                detail TEXT
            );
            """
        )


def log_operation(action: str, target: str = "", detail: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO operation_logs(action, target, created_at, detail) VALUES (?, ?, ?, ?)",
            (action, target, utc_now().isoformat(), detail),
        )


def require_auth(request: Request) -> None:
    if not request.session.get("authed"):
        raise HTTPException(status_code=303, detail="login required")


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content
    fm_raw = match.group(1)
    data = yaml.safe_load(fm_raw) or {}
    body = content[match.end() :]
    return data, body


def dump_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    fm = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{body.lstrip()}"


def read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, ""
    content = path.read_text(encoding="utf-8")
    return parse_frontmatter(content)


def write_markdown(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup_file(path)
    content = dump_frontmatter(frontmatter, body)
    path.write_text(content, encoding="utf-8")
    word_count = count_words(body)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO file_index(path, project, word_count, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                project=excluded.project,
                word_count=excluded.word_count,
                updated_at=excluded.updated_at
            """,
            (
                str(path),
                path.parts[-3] if len(path.parts) >= 3 else "",
                word_count,
                utc_now().isoformat(),
            ),
        )


def backup_file(path: Path) -> None:
    rel = path.relative_to(VAULT_ROOT) if path.is_relative_to(VAULT_ROOT) else Path(path.name)
    backup_name = f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}__{path.name}"
    dest = BACKUP_ROOT / rel.parent / backup_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)


def count_words(text: str) -> int:
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    latin = re.findall(r"[A-Za-z0-9_]+", text)
    return len(cjk) + len(latin)


def scan_projects() -> list[dict[str, Any]]:
    if not NOVELS_ROOT.exists():
        return []
    projects = []
    for project_dir in sorted([p for p in NOVELS_ROOT.iterdir() if p.is_dir()]):
        chapter_dir = project_dir / "chapters"
        chapters = list(chapter_dir.glob("*.md")) if chapter_dir.exists() else []
        total_words = 0
        latest = None
        for chapter in chapters:
            _, body = read_markdown(chapter)
            total_words += count_words(body)
            mtime = datetime.fromtimestamp(chapter.stat().st_mtime, tz=timezone.utc)
            latest = mtime if latest is None or mtime > latest else latest
        projects.append(
            {
                "name": project_dir.name,
                "path": project_dir,
                "chapters": len(chapters),
                "total_words": total_words,
                "latest": latest,
            }
        )
    return projects


def list_chapters(project: str) -> list[dict[str, Any]]:
    chapter_dir = NOVELS_ROOT / project / "chapters"
    if not chapter_dir.exists():
        return []
    rows = []
    for f in sorted(chapter_dir.glob("*.md")):
        fm, body = read_markdown(f)
        rows.append(
            {
                "filename": f.name,
                "title": fm.get("title", f.stem),
                "chapter": fm.get("chapter", ""),
                "status": fm.get("status", "draft"),
                "word_count": count_words(body),
                "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc),
            }
        )
    return rows


def list_notes(project: str, folder: str) -> list[dict[str, Any]]:
    note_dir = NOVELS_ROOT / project / folder
    if not note_dir.exists():
        return []
    items = []
    for f in sorted(note_dir.glob("*.md")):
        items.append(
            {
                "name": f.stem,
                "filename": f.name,
                "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc),
            }
        )
    return items


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, password: str = Form(...)) -> Response:
    if not ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "NOVELHUB_PASSWORD not configured"},
            status_code=500,
        )
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "密码错误"}, status_code=401
        )
    request.session["authed"] = True
    log_operation("login", detail="admin login")
    return RedirectResponse(url="/", status_code=303)


@app.get("/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse(url="/login", status_code=303)
    projects = scan_projects()
    total_words = sum(p["total_words"] for p in projects)
    today = utc_now().date()
    today_added = 0
    recent_chapters: list[dict[str, Any]] = []
    for p in projects:
        for c in list_chapters(p["name"]):
            if c["modified"].date() == today:
                today_added += c["word_count"]
            recent_chapters.append({"project": p["name"], **c})
    recent_chapters.sort(key=lambda x: x["modified"], reverse=True)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "projects": projects,
            "total_words": total_words,
            "today_added": today_added,
            "recent_chapters": recent_chapters[:12],
        },
    )


@app.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse(url="/login", status_code=303)
    projects = scan_projects()
    return templates.TemplateResponse("projects.html", {"request": request, "projects": projects})


@app.get("/projects/{project}/chapters", response_class=HTMLResponse)
def chapters_page(request: Request, project: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse(url="/login", status_code=303)
    chapters = list_chapters(project)
    return templates.TemplateResponse(
        "chapters.html", {"request": request, "project": project, "chapters": chapters}
    )


@app.get("/projects/{project}/editor/{filename}", response_class=HTMLResponse)
def editor_page(request: Request, project: str, filename: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse(url="/login", status_code=303)
    path = NOVELS_ROOT / project / "chapters" / filename
    fm, body = read_markdown(path)
    return templates.TemplateResponse(
        "editor.html",
        {
            "request": request,
            "project": project,
            "filename": filename,
            "frontmatter": fm,
            "body": body,
        },
    )


@app.post("/projects/{project}/editor/{filename}", response_class=HTMLResponse)
def save_chapter(
    request: Request,
    project: str,
    filename: str,
    title: str = Form(""),
    chapter: str = Form(""),
    status: str = Form("draft"),
    volume: str = Form(""),
    tags: str = Form(""),
    body: str = Form(""),
) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse(url="/login", status_code=303)
    path = NOVELS_ROOT / project / "chapters" / filename
    frontmatter = {
        "title": title,
        "chapter": chapter,
        "status": status,
        "volume": volume,
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
    }
    write_markdown(path, frontmatter, body)
    log_operation("save", str(path), f"words={count_words(body)}")
    return templates.TemplateResponse(
        "_save_result.html",
        {"request": request, "saved_at": utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")},
    )


@app.post("/projects/{project}/preview", response_class=HTMLResponse)
def preview_markdown(request: Request, body: str = Form("")) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse(url="/login", status_code=303)
    html = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = html.replace("\n", "<br>")
    return templates.TemplateResponse("_preview.html", {"request": request, "html": html})


@app.get("/projects/{project}/characters", response_class=HTMLResponse)
def characters_page(request: Request, project: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse(url="/login", status_code=303)
    items = list_notes(project, "characters")
    return templates.TemplateResponse(
        "notes.html",
        {"request": request, "project": project, "title": "人物卡", "items": items, "folder": "characters"},
    )


@app.get("/projects/{project}/world", response_class=HTMLResponse)
def world_page(request: Request, project: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse(url="/login", status_code=303)
    items = list_notes(project, "world")
    return templates.TemplateResponse(
        "notes.html",
        {"request": request, "project": project, "title": "世界观", "items": items, "folder": "world"},
    )


@app.get("/projects/{project}/export")
def export_project(request: Request, project: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse(url="/login", status_code=303)
    chapter_dir = NOVELS_ROOT / project / "chapters"
    if not chapter_dir.exists():
        raise HTTPException(status_code=404, detail="project not found")
    merged = []
    for f in sorted(chapter_dir.glob("*.md")):
        fm, body = read_markdown(f)
        merged.append(f"# {fm.get('title', f.stem)}\n\n{body.strip()}\n")
    content = "\n\n".join(merged)
    log_operation("export", project, f"chapters={len(merged)}")
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{project}.txt"'},
    )
