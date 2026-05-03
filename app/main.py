from __future__ import annotations

import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any

import markdown
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.services.ai_client import generate_ai_content
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
VAULT_ROOT = Path(os.getenv("NOVELHUB_VAULT_ROOT", "/root/ObsidianVault")).expanduser()
NOVELS_ROOT = VAULT_ROOT / "Novels"
BACKUP_ROOT = Path(os.getenv("NOVELHUB_BACKUP_ROOT", str(VAULT_ROOT / ".novelhub-backups"))).expanduser()
DB_PATH = Path(os.getenv("NOVELHUB_DB_PATH", str(BASE_DIR / "novelhub.db"))).expanduser()
ADMIN_PASSWORD = os.getenv("NOVELHUB_PASSWORD", "")
SECRET_KEY = os.getenv("NOVELHUB_SECRET_KEY", "change-me")
DAILY_GOAL_WORDS = int(os.getenv("NOVELHUB_DAILY_GOAL", "2000"))
PROJECT_GOAL_WORDS = int(os.getenv("NOVELHUB_PROJECT_GOAL", "100000"))

app = FastAPI(title="Novel Hub")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

def inject_locale(request: Request):
    return get_setting("locale", "zh-CN")

templates.env.globals["get_locale"] = inject_locale

FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)
STATUS_ORDER = ["idea", "outline", "draft", "rewrite", "polish", "done", "published"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_slug(value: str, fallback: str = "untitled") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\-_\u4e00-\u9fff]+", "-", value.strip())
    cleaned = cleaned.strip("-")
    return cleaned or fallback


def _ensure_under_root(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if not str(resolved).startswith(str(root_resolved)):
        raise HTTPException(status_code=400, detail="invalid path")
    return resolved


def require_auth(request: Request) -> None:
    if not request.session.get("authed"):
        raise HTTPException(status_code=401, detail="auth required")


def get_setting(key: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )


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

            CREATE TABLE IF NOT EXISTS ai_pipelines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                stage TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def log_operation(action: str, target: str = "", detail: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO operation_logs(action, target, created_at, detail) VALUES (?, ?, ?, ?)",
            (action, target, utc_now().isoformat(), detail),
        )


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content
    data = yaml.safe_load(match.group(1)) or {}
    body = content[match.end() :]
    return data, body


def dump_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    frontmatter_clean = {k: v for k, v in frontmatter.items() if v not in (None, "", [])}
    fm = yaml.safe_dump(frontmatter_clean, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{body}"


def read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    p = _ensure_under_root(path, VAULT_ROOT)
    if not p.exists():
        return {}, ""
    content = p.read_text(encoding="utf-8")
    return parse_frontmatter(content)


def backup_file(path: Path) -> None:
    p = _ensure_under_root(path, VAULT_ROOT)
    if not p.exists():
        return
    rel = p.relative_to(VAULT_ROOT)
    backup_name = f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}__{p.name}"
    dest = BACKUP_ROOT / rel.parent / backup_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dest)


def count_words(text: str) -> int:
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    latin = re.findall(r"[A-Za-z0-9_]+", text)
    return len(cjk) + len(latin)


def chapter_path(project: str, filename: str) -> Path:
    safe_project = safe_slug(project, fallback="project")
    safe_file = safe_slug(filename.replace(".md", ""), fallback="chapter") + ".md"
    path = NOVELS_ROOT / safe_project / "chapters" / safe_file
    return _ensure_under_root(path, VAULT_ROOT)


def project_path(project: str) -> Path:
    path = NOVELS_ROOT / safe_slug(project, fallback="project")
    return _ensure_under_root(path, VAULT_ROOT)


def list_markdown_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(folder.glob("*.md"))


def normalize_meta(fm: dict[str, Any], stem: str) -> dict[str, Any]:
    return {
        "title": fm.get("title", stem),
        "chapter": fm.get("chapter", ""),
        "status": fm.get("status", "draft"),
        "volume": fm.get("volume", ""),
        "tags": fm.get("tags", []),
        "synopsis": fm.get("synopsis", ""),
        "notes": fm.get("notes", ""),
        "pov": fm.get("pov", ""),
        "characters": fm.get("characters", []),
        "locations": fm.get("locations", []),
        "warnings": fm.get("warnings", []),
        "draft_version": fm.get("draft_version", ""),
    }


def list_chapters(project: str) -> list[dict[str, Any]]:
    folder = project_path(project) / "chapters"
    rows: list[dict[str, Any]] = []
    for f in list_markdown_files(folder):
        fm, body = read_markdown(f)
        meta = normalize_meta(fm, f.stem)
        rows.append(
            {
                "filename": f.name,
                "title": meta["title"],
                "chapter": meta["chapter"],
                "status": meta["status"],
                "volume": meta["volume"],
                "word_count": count_words(body),
                "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc),
                "meta": meta,
            }
        )
    return rows


def list_notes(project: str, folder_name: str) -> list[dict[str, Any]]:
    folder = project_path(project) / folder_name
    rows = []
    for f in list_markdown_files(folder):
        _, body = read_markdown(f)
        excerpt = body.strip().replace("\n", " ")[:120]
        rows.append(
            {
                "name": f.stem,
                "filename": f.name,
                "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc),
                "word_count": count_words(body),
                "excerpt": excerpt,
            }
        )
    return rows


def scan_projects() -> list[dict[str, Any]]:
    if not NOVELS_ROOT.exists():
        return []
    results = []
    for p in sorted([item for item in NOVELS_ROOT.iterdir() if item.is_dir()]):
        project = p.name
        chapters = list_chapters(project)
        chars = list_notes(project, "characters")
        world = list_notes(project, "world")
        total_words = sum(c["word_count"] for c in chapters)
        latest = max((c["modified"] for c in chapters), default=None)
        results.append(
            {
                "name": project,
                "status": "active" if chapters else "planning",
                "chapter_count": len(chapters),
                "character_count": len(chars),
                "world_count": len(world),
                "total_words": total_words,
                "target_words": PROJECT_GOAL_WORDS,
                "progress": min(100, int(total_words / PROJECT_GOAL_WORDS * 100)) if PROJECT_GOAL_WORDS else 0,
                "latest": latest,
            }
        )
    return results


def write_markdown(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    p = _ensure_under_root(path, VAULT_ROOT)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        backup_file(p)
    content = dump_frontmatter(frontmatter, body)
    p.write_text(content, encoding="utf-8")
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
            (str(p), p.parts[-3] if len(p.parts) >= 3 else "", count_words(body), utc_now().isoformat()),
        )


def parse_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def trend_placeholder() -> list[dict[str, Any]]:
    return [{"day": f"D-{i}", "words": 0} for i in range(6, -1, -1)]


@app.on_event("startup")
def startup() -> None:
    NOVELS_ROOT.mkdir(parents=True, exist_ok=True)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> Response:
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, password: str = Form(...)) -> Response:
    if not ADMIN_PASSWORD:
        return templates.TemplateResponse("login.html", {"request": request, "error": "NOVELHUB_PASSWORD 未配置"}, status_code=500)
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse("login.html", {"request": request, "error": "密码错误"}, status_code=401)
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
        return RedirectResponse("/login", status_code=303)
    projects = scan_projects()
    chapters = []
    for p in projects:
        for c in list_chapters(p["name"]):
            chapters.append({"project": p["name"], **c})
    chapters.sort(key=lambda x: x["modified"], reverse=True)
    today = utc_now().date()
    today_words = sum(c["word_count"] for c in chapters if c["modified"].date() == today)
    quick_project = projects[0]["name"] if projects else None
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "projects": projects,
            "today_words": today_words,
            "total_words": sum(p["total_words"] for p in projects),
            "recent_chapters": chapters[:8],
            "trend": trend_placeholder(),
            "daily_goal": DAILY_GOAL_WORDS,
            "quick_project": quick_project,
        },
    )


@app.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("projects.html", {"request": request, "projects": scan_projects()})

@app.get("/ai-pipeline", response_class=HTMLResponse)
def ai_pipeline_global(request: Request) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("ai/pipeline.html", {"request": request, "projects": scan_projects(), "project": None})

@app.get("/ai-pipeline/{project}", response_class=HTMLResponse)
def ai_pipeline_project(request: Request, project: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    safe_project = safe_slug(project, fallback="project")

    # Load pipeline data from DB
    stages_data = {}
    with get_conn() as conn:
        rows = conn.execute("SELECT stage, data FROM ai_pipelines WHERE project = ?", (safe_project,)).fetchall()
        for row in rows:
            try:
                stages_data[row["stage"]] = json.loads(row["data"])
            except:
                pass

    return templates.TemplateResponse("ai/pipeline.html", {
        "request": request,
        "projects": scan_projects(),
        "project": safe_project,
        "stages_data": stages_data
    })

@app.post("/ai-pipeline/{project}/save")
async def ai_pipeline_save(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()

    with get_conn() as conn:
        for stage_data in data:
            stage_id = stage_data.get("id")
            if not stage_id: continue

            # Delete old data for this stage
            conn.execute("DELETE FROM ai_pipelines WHERE project = ? AND stage = ?", (safe_project, stage_id))

            # Insert new data
            conn.execute(
                "INSERT INTO ai_pipelines(project, stage, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (safe_project, stage_id, json.dumps(stage_data), utc_now().isoformat(), utc_now().isoformat())
            )

    return JSONResponse(content={"status": "ok"})

@app.post("/ai-pipeline/select-project")
def ai_pipeline_select(request: Request, project: str = Form(...)) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    return RedirectResponse(url=f"/ai-pipeline/{safe_project}", status_code=303)

@app.post("/ai-pipeline/{project}/generate")
async def ai_pipeline_generate(request: Request, project: str) -> Response:
    require_auth(request)
    data = await request.json()
    stage = data.get("stage", "")
    context = data.get("context", "")

    api_key = get_setting("ai_api_key", "")
    base_url = get_setting("ai_base_url", "https://api.openai.com/v1")
    model = get_setting("ai_model", "gpt-3.5-turbo")

    if not api_key:
        return JSONResponse(status_code=400, content={"error": "AI API Key 未配置，请前往设置页面配置。"})

    system_prompt = "你是一个专业的网文小说创作助手。"

    if stage == "world":
        system_prompt += "请根据用户的初步想法，生成几个核心角色和世界观设定的卡片节点。每个节点需要有标题和详细描述。"
    elif stage == "outline":
        system_prompt += "请根据世界观和角色设定，生成故事的分卷大纲节点。"
    elif stage == "chapters":
        system_prompt += "请根据分卷大纲，将其拆解为具体的章节细纲节点。"
    elif stage == "draft":
        system_prompt += "请根据章节细纲，扩写出几千字的正文草稿（这里我们先生成一个梗概演示）。"

    user_prompt = f"当前上下文信息：\n{context}\n\n请为我生成下一步的创作内容，请直接返回内容，条理清晰。"

    content = await generate_ai_content(api_key, base_url, model, system_prompt, user_prompt)

    if not content:
        return JSONResponse(status_code=500, content={"error": "AI 生成失败，请检查配置或网络。"})

    return JSONResponse(content={"result": content})


@app.post("/projects/new", response_class=HTMLResponse)
def create_project(request: Request, name: str = Form(...)) -> Response:
    require_auth(request)
    project = safe_slug(name, fallback="MyNovel")
    root = project_path(project)
    (root / "chapters").mkdir(parents=True, exist_ok=True)
    (root / "characters").mkdir(parents=True, exist_ok=True)
    (root / "world").mkdir(parents=True, exist_ok=True)
    log_operation("create_project", project)
    return RedirectResponse(url=f"/projects/{project}", status_code=303)


@app.get("/projects/{project}", response_class=HTMLResponse)
def project_detail(request: Request, project: str, status: str | None = None) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    safe_project = safe_slug(project, fallback="project")
    project_info = next((p for p in scan_projects() if p["name"] == safe_project), None)
    if not project_info:
        raise HTTPException(status_code=404, detail="project not found")
    chapters = list_chapters(safe_project)
    if status and status in STATUS_ORDER:
        chapters = [c for c in chapters if c["status"] == status]
    recent = sorted(chapters, key=lambda x: x["modified"], reverse=True)[:5]
    return templates.TemplateResponse(
        "project_detail.html",
        {
            "request": request,
            "project": safe_project,
            "project_info": project_info,
            "chapters": chapters,
            "recent": recent,
            "status_filter": status or "all",
            "status_options": STATUS_ORDER,
        },
    )


@app.post("/projects/{project}/chapters/new")
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
    filename = f"{idx:03d}-{safe_slug(title, fallback='chapter')}"
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
    log_operation("create_chapter", str(path))
    return RedirectResponse(url=f"/projects/{safe_project}/editor/{path.name}", status_code=303)


@app.get("/projects/{project}/chapters", response_class=HTMLResponse)
def chapters_page_redirect(request: Request, project: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse(url=f"/projects/{safe_slug(project, fallback='project')}", status_code=303)


@app.get("/projects/{project}/editor/{filename}", response_class=HTMLResponse)
def editor_page(request: Request, project: str, filename: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    safe_project = safe_slug(project, fallback="project")
    path = chapter_path(safe_project, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="chapter not found")
    fm, body = read_markdown(path)
    meta = normalize_meta(fm, path.stem)
    chapters = list_chapters(safe_project)
    active = next((c for c in chapters if c["filename"] == path.name), None)
    return templates.TemplateResponse(
        "editor.html",
        {
            "request": request,
            "project": safe_project,
            "filename": path.name,
            "frontmatter": meta,
            "body": body,
            "chapters": chapters,
            "active": active,
            "project_words": sum(c["word_count"] for c in chapters),
            "goal": PROJECT_GOAL_WORDS,
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
    synopsis: str = Form(""),
    notes: str = Form(""),
    pov: str = Form(""),
    characters: str = Form(""),
    locations: str = Form(""),
    warnings: str = Form(""),
    draft_version: str = Form(""),
    body: str = Form(""),
) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    path = chapter_path(safe_project, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="chapter not found")
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
    write_markdown(path, frontmatter, body)
    log_operation("save", str(path), f"words={count_words(body)}")
    return templates.TemplateResponse(
        "_save_result.html",
        {"request": request, "saved_at": utc_now().strftime("%Y-%m-%d %H:%M:%S UTC"), "word_count": count_words(body)},
    )


@app.post("/projects/{project}/preview", response_class=HTMLResponse)
def preview_markdown(request: Request, body: str = Form("")) -> Response:
    require_auth(request)
    html = markdown.markdown(body, extensions=["fenced_code", "tables"])
    return templates.TemplateResponse("_preview.html", {"request": request, "html": html})


@app.get("/projects/{project}/characters", response_class=HTMLResponse)
def characters_page(request: Request, project: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    safe_project = safe_slug(project, fallback="project")
    items = list_notes(safe_project, "characters")
    return templates.TemplateResponse("characters.html", {"request": request, "project": safe_project, "items": items})


@app.get("/projects/{project}/world", response_class=HTMLResponse)
def world_page(request: Request, project: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    safe_project = safe_slug(project, fallback="project")
    items = list_notes(safe_project, "world")
    categories = {"locations": [], "organizations": [], "items": [], "timeline": []}
    for item in items:
        key = "locations"
        for candidate in categories:
            if item["name"].lower().startswith(candidate[:3]):
                key = candidate
                break
        categories[key].append(item)
    return templates.TemplateResponse("world.html", {"request": request, "project": safe_project, "items": items, "categories": categories})


@app.get("/projects/{project}/notes/{folder}/{filename}", response_class=HTMLResponse)
def note_preview(request: Request, project: str, folder: str, filename: str) -> Response:
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
    html = markdown.markdown(body, extensions=["fenced_code", "tables"])
    return templates.TemplateResponse("_note_preview.html", {"request": request, "title": p.stem, "html": html})


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


@app.get("/projects/{project}/export")
def export_download(request: Request, project: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    safe_project = safe_slug(project, fallback="project")
    chapter_dir = project_path(safe_project) / "chapters"
    if not chapter_dir.exists():
        raise HTTPException(status_code=404, detail="project not found")
    merged = []
    for f in list_markdown_files(chapter_dir):
        fm, body = read_markdown(f)
        merged.append(f"# {fm.get('title', f.stem)}\n\n{body.strip()}\n")
    content = "\n\n".join(merged)
    return Response(content=content, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{safe_project}.txt"'})


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    with get_conn() as conn:
        log_count = conn.execute("SELECT COUNT(1) as c FROM operation_logs").fetchone()["c"]

    ai_api_key = get_setting("ai_api_key", "")
    ai_base_url = get_setting("ai_base_url", "https://api.openai.com/v1")
    ai_model = get_setting("ai_model", "gpt-3.5-turbo")

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "vault_root": str(VAULT_ROOT),
            "db_path": str(DB_PATH),
            "backup_root": str(BACKUP_ROOT),
            "login_state": "已登录",
            "log_count": log_count,
            "ai_api_key": ai_api_key,
            "ai_base_url": ai_base_url,
            "ai_model": ai_model,
        },
    )

@app.post("/settings/ai")
def update_ai_settings(
    request: Request,
    ai_api_key: str = Form(""),
    ai_base_url: str = Form(""),
    ai_model: str = Form(""),
) -> Response:
    require_auth(request)
    set_setting("ai_api_key", ai_api_key)
    set_setting("ai_base_url", ai_base_url)
    set_setting("ai_model", ai_model)
    log_operation("update_ai_settings")
    return RedirectResponse(url="/settings", status_code=303)

@app.post("/settings/locale")
def update_locale(request: Request, locale: str = Form(...)) -> Response:
    require_auth(request)
    if locale in ["zh-CN", "en-US", "ja-JP"]:
        set_setting("locale", locale)
    log_operation("update_locale", detail=locale)
    # Redirect back to the referrer or home
    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=referer, status_code=303)
