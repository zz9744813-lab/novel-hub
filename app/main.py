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
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA mmap_size=268435456")
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
                updated_at TEXT,
                title TEXT,
                chapter TEXT,
                chapter_int INTEGER,
                status TEXT,
                volume TEXT,
                mtime REAL
            );

            CREATE INDEX IF NOT EXISTS idx_fi_project ON file_index(project, chapter_int);

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

            CREATE TABLE IF NOT EXISTS project_meta (
                project TEXT PRIMARY KEY,
                target_words INTEGER DEFAULT 100000,
                author TEXT DEFAULT '',
                synopsis TEXT DEFAULT '',
                daily_goal INTEGER DEFAULT 2000
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chapter_fts USING fts5(
                path UNINDEXED,
                project UNINDEXED,
                title,
                body,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        try:
            conn.execute("ALTER TABLE file_index ADD COLUMN title TEXT")
            conn.execute("ALTER TABLE file_index ADD COLUMN chapter TEXT")
            conn.execute("ALTER TABLE file_index ADD COLUMN chapter_int INTEGER")
            conn.execute("ALTER TABLE file_index ADD COLUMN status TEXT")
            conn.execute("ALTER TABLE file_index ADD COLUMN volume TEXT")
            conn.execute("ALTER TABLE file_index ADD COLUMN mtime REAL")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fi_project ON file_index(project, chapter_int)")
        except sqlite3.OperationalError:
            pass



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
    dest_dir = BACKUP_ROOT / rel.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / backup_name
    shutil.copy2(p, dest)
    
    all_backups = sorted(list(dest_dir.glob(f"*__{p.name}")), key=lambda x: x.name)
    if len(all_backups) > 20:
        for old in all_backups[:-20]:
            try:
                old.unlink()
            except Exception:
                pass


def count_words(text: str) -> int:
    cjk = re.findall(r"[\u4e00-\u9fff]", text)
    latin = re.findall(r"[A-Za-z0-9_]+", text)
    return len(cjk) + len(latin)


def chapter_path(project: str, filename: str, volume: str = None) -> Path:
    safe_project = safe_slug(project, fallback="project")
    safe_file = safe_slug(filename.replace(".md", ""), fallback="chapter") + ".md"
    base_folder = NOVELS_ROOT / safe_project / "chapters"
    
    if volume is not None:
        vol_folder = safe_slug(volume, fallback="volume-01") if volume else "volume-01"
        return _ensure_under_root(base_folder / vol_folder / safe_file, VAULT_ROOT)
        
    for f in base_folder.rglob(safe_file):
        return _ensure_under_root(f, VAULT_ROOT)
        
    return _ensure_under_root(base_folder / "volume-01" / safe_file, VAULT_ROOT)


def project_path(project: str) -> Path:
    path = NOVELS_ROOT / safe_slug(project, fallback="project")
    return _ensure_under_root(path, VAULT_ROOT)


def list_markdown_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    if folder.name == "chapters":
        return sorted(folder.rglob("*.md"))
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
    with get_conn() as conn:
        db_rows = conn.execute(
            "SELECT path, word_count, updated_at, title, chapter, chapter_int, status, volume, mtime "
            "FROM file_index WHERE project=? ORDER BY chapter_int, path",
            (project,)
        ).fetchall()
        
        cached_map = {r["path"]: dict(r) for r in db_rows}
        
        for f in list_markdown_files(folder):
            mtime = f.stat().st_mtime
            f_str = str(f)
            cached = cached_map.get(f_str)
            
            if not cached or cached.get("mtime") != mtime:
                fm, body = read_markdown(f)
                meta = normalize_meta(fm, f.stem)
                words = count_words(body)
                
                try:
                    chapter_int = int(meta["chapter"])
                except ValueError:
                    chapter_int = 0
                    
                conn.execute(
                    """
                    INSERT INTO file_index(path, project, word_count, updated_at, title, chapter, chapter_int, status, volume, mtime)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        project=excluded.project,
                        word_count=excluded.word_count,
                        updated_at=excluded.updated_at,
                        title=excluded.title,
                        chapter=excluded.chapter,
                        chapter_int=excluded.chapter_int,
                        status=excluded.status,
                        volume=excluded.volume,
                        mtime=excluded.mtime
                    """,
                    (f_str, project, words, utc_now().isoformat(), meta["title"], meta["chapter"], chapter_int, meta["status"], meta["volume"], mtime)
                )
                
                conn.execute("DELETE FROM chapter_fts WHERE path=?", (f_str,))
                conn.execute("INSERT INTO chapter_fts(path, project, title, body) VALUES (?, ?, ?, ?)", 
                             (f_str, project, meta["title"], body))
                
                rows.append({
                    "filename": f.name,
                    "title": meta["title"],
                    "chapter": meta["chapter"],
                    "status": meta["status"],
                    "volume": meta["volume"],
                    "word_count": words,
                    "modified": datetime.fromtimestamp(mtime, tz=timezone.utc),
                    "meta": meta,
                })
            else:
                rows.append({
                    "filename": f.name,
                    "title": cached["title"],
                    "chapter": cached["chapter"],
                    "status": cached["status"],
                    "volume": cached["volume"],
                    "word_count": cached["word_count"],
                    "modified": datetime.fromtimestamp(cached["mtime"], tz=timezone.utc),
                    "meta": {
                        "title": cached["title"],
                        "chapter": cached["chapter"],
                        "status": cached["status"],
                        "volume": cached["volume"]
                    }
                })

    def _sort_key(x):
        try:
            c_int = int(x["chapter"])
        except ValueError:
            c_int = 0
        return (c_int, x["filename"])
        
    rows.sort(key=_sort_key)
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


def get_project_meta(project: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM project_meta WHERE project=?", (project,)).fetchone()
        if row:
            return dict(row)
        return {"project": project, "target_words": PROJECT_GOAL_WORDS, "author": "", "synopsis": "", "daily_goal": DAILY_GOAL_WORDS}


def set_project_meta(project: str, target_words: int = 100000, author: str = "", synopsis: str = "", daily_goal: int = 2000) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO project_meta(project, target_words, author, synopsis, daily_goal)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(project) DO UPDATE SET
                   target_words=excluded.target_words,
                   author=excluded.author,
                   synopsis=excluded.synopsis,
                   daily_goal=excluded.daily_goal""",
            (project, target_words, author, synopsis, daily_goal)
        )


def scan_projects() -> list[dict[str, Any]]:
    if not NOVELS_ROOT.exists():
        return []
    results = []
    for p in sorted([item for item in NOVELS_ROOT.iterdir() if item.is_dir()]):
        project = p.name
        chapters = list_chapters(project)
        chars = list_notes(project, "characters")
        world = list_notes(project, "world")
        meta = get_project_meta(project)
        target = meta.get("target_words", PROJECT_GOAL_WORDS) or PROJECT_GOAL_WORDS
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
                "target_words": target,
                "progress": min(100, int(total_words / target * 100)) if target else 0,
                "latest": latest,
                "author": meta.get("author", ""),
                "synopsis": meta.get("synopsis", ""),
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
    
    meta = normalize_meta(frontmatter, p.stem)
    words = count_words(body)
    mtime = p.stat().st_mtime
    try:
        chapter_int = int(meta["chapter"])
    except ValueError:
        chapter_int = 0
        
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO file_index(path, project, word_count, updated_at, title, chapter, chapter_int, status, volume, mtime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                project=excluded.project,
                word_count=excluded.word_count,
                updated_at=excluded.updated_at,
                title=excluded.title,
                chapter=excluded.chapter,
                chapter_int=excluded.chapter_int,
                status=excluded.status,
                volume=excluded.volume,
                mtime=excluded.mtime
            """,
            (str(p), p.parts[-4] if len(p.parts) >= 4 else "", words, utc_now().isoformat(), meta["title"], meta["chapter"], chapter_int, meta["status"], meta["volume"], mtime),
        )
        conn.execute("DELETE FROM chapter_fts WHERE path=?", (str(p),))
        conn.execute("INSERT INTO chapter_fts(path, project, title, body) VALUES (?, ?, ?, ?)", 
                     (str(p), p.parts[-4] if len(p.parts) >= 4 else "", meta["title"], body))


def parse_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def compute_trend() -> list[dict[str, Any]]:
    from datetime import timedelta
    trend = []
    now = utc_now()

    with get_conn() as conn:
        for i in range(6, -1, -1):
            day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)

            rows = conn.execute(
                "SELECT detail FROM operation_logs WHERE action = 'save' AND created_at >= ? AND created_at < ?",
                (day_start.isoformat(), day_end.isoformat())
            ).fetchall()

            words_added = 0
            for r in rows:
                try:
                    words_added += int(r["detail"].replace("words_added=", ""))
                except:
                    pass

            trend.append({
                "day": day_start.strftime("%m-%d"),
                "words": words_added
            })
    return trend


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
        return templates.TemplateResponse("login.html", {"request": request, "error": "系统未初始化 (NOVELHUB_PASSWORD 未配置)，请联系管理员。"}, status_code=200)
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

    # Calculate today's words from operation logs
    trend_data = compute_trend()
    today_words = trend_data[-1]["words"] if trend_data else 0

    # Better logic for quick project: the project with the most recently modified chapter
    quick_project = chapters[0]["project"] if chapters else (projects[0]["name"] if projects else None)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "projects": projects,
            "today_words": today_words,
            "total_words": sum(p["total_words"] for p in projects),
            "recent_chapters": chapters[:8],
            "trend": trend_data,
            "daily_goal": DAILY_GOAL_WORDS,
            "quick_project": quick_project,
        },
    )

@app.post("/examples/create", response_class=HTMLResponse)
def create_example_project(request: Request) -> Response:
    require_auth(request)
    project = "DemoProject"
    root = project_path(project)
    if not root.exists():
        (root / "chapters").mkdir(parents=True, exist_ok=True)
        (root / "characters").mkdir(parents=True, exist_ok=True)
        (root / "world").mkdir(parents=True, exist_ok=True)
        log_operation("create_project", project)

        # Add sample chapter
        path = chapter_path(project, "001-demo-chapter")
        frontmatter = {
            "title": "Welcome to Novel Hub",
            "chapter": "1",
            "status": "draft",
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
        write_markdown(path, frontmatter, "This is a demo chapter generated by the system. You can start typing your story here!")
        log_operation("create_chapter", str(path))

    return RedirectResponse(url=f"/projects/{project}", status_code=303)


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

@app.post("/ai-pipeline/{project}/apply")
async def ai_pipeline_apply(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    stage = data.get("stage", "")
    title = data.get("title", "AI 节点")
    content = data.get("content", "")

    if stage in ["chapters", "draft"]:
        existing = list_chapters(safe_project)
        idx = len(existing) + 1
        filename = f"{idx:05d}-{safe_slug(title, fallback='chapter')}"
        path = chapter_path(safe_project, filename)
        frontmatter = {
            "title": title,
            "chapter": str(idx),
            "status": "draft",
            "volume": "",
            "tags": [],
            "synopsis": "",
            "notes": "Generated by AI Pipeline",
            "pov": "",
            "characters": [],
            "locations": [],
            "warnings": [],
            "draft_version": "v1",
        }
        write_markdown(path, frontmatter, content)
        log_operation("create_chapter_from_ai", str(path))
        return JSONResponse(content={"status": "ok", "redirect_url": f"/projects/{safe_project}/editor/{path.name}"})

    elif stage in ["world", "outline"]:
        folder = "characters" if "角色" in title or "主角" in title or stage == "outline" else "world"
        p = project_path(safe_project) / folder / (safe_slug(title, fallback="note") + ".md")
        p = _ensure_under_root(p, VAULT_ROOT)
        p.parent.mkdir(parents=True, exist_ok=True)
        # We write simply without heavy frontmatter for notes for now
        write_markdown(p, {"title": title, "tags": ["ai-generated"]}, content)
        log_operation("create_note_from_ai", str(p))
        return JSONResponse(content={"status": "ok", "redirect_url": f"/projects/{safe_project}/{folder}"})

    return JSONResponse(status_code=400, content={"error": "Unknown stage"})

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

    system_prompt = "你是一个专业的网文小说创作助手。你必须严格返回一段合法的 JSON 数组，其中每个元素是一个对象，包含 'title' 和 'content' 两个字符串字段。不要返回任何其他内容（不要用 Markdown 代码块包裹，也不要有任何前后缀文字）。"

    if stage == "world":
        system_prompt += "请根据用户的初步想法，生成几个核心角色和世界观设定的卡片节点。"
    elif stage == "outline":
        system_prompt += "请根据世界观和角色设定，生成故事的分卷大纲节点。"
    elif stage == "chapters":
        system_prompt += "请根据分卷大纲，将其拆解为具体的章节细纲节点。"
    elif stage == "draft":
        system_prompt += "请根据章节细纲，扩写出正文草稿节点（按段落或场景拆分）。"

    user_prompt = f"当前上下文信息：\n{context}\n\n请为我生成下一步的创作内容，返回格式必须是类似 [{{\"title\": \"标题\", \"content\": \"详细内容\"}}] 的 JSON 数组。"

    content = await generate_ai_content(api_key, base_url, model, system_prompt, user_prompt)

    if not content:
        return JSONResponse(status_code=500, content={"error": "AI 生成失败，请检查配置或网络。"})

    try:
        # Strip potential markdown formatting that some models stubbornly add
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]

        parsed_cards = json.loads(content.strip())
        if not isinstance(parsed_cards, list):
            raise ValueError("Result is not a list")
    except Exception as e:
        # Fallback to single card if parsing fails
        parsed_cards = [{"title": "AI 生成内容", "content": content}]

    return JSONResponse(content={"result": parsed_cards})


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

@app.delete("/projects/{project}")
def delete_project(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    p = project_path(safe_project)
    if p.exists():
        shutil.rmtree(p)
        with get_conn() as conn:
            conn.execute("DELETE FROM file_index WHERE project=?", (safe_project,))
            conn.execute("DELETE FROM ai_pipelines WHERE project=?", (safe_project,))
        log_operation("delete_project", safe_project)
    return JSONResponse(content={"status": "ok"})

@app.put("/projects/{project}/rename")
async def rename_project(request: Request, project: str) -> Response:
    require_auth(request)
    data = await request.json()
    new_name = safe_slug(data.get("name", ""), fallback="project")
    if not new_name:
        raise HTTPException(status_code=400)
    
    safe_project = safe_slug(project, fallback="project")
    old_p = project_path(safe_project)
    new_p = NOVELS_ROOT / new_name
    
    if new_p.exists():
        raise HTTPException(status_code=400, detail="Project already exists")
        
    if old_p.exists():
        old_p.rename(new_p)
        with get_conn() as conn:
            rows = conn.execute("SELECT path FROM file_index WHERE project=?", (safe_project,)).fetchall()
            for r in rows:
                old_path = r["path"]
                new_path = old_path.replace(str(old_p), str(new_p), 1)
                conn.execute("UPDATE file_index SET path=?, project=? WHERE path=?", (new_path, new_name, old_path))
            conn.execute("UPDATE ai_pipelines SET project=? WHERE project=?", (new_name, safe_project))
        log_operation("rename_project", f"{safe_project} -> {new_name}")
        
    return JSONResponse(content={"status": "ok", "new_url": f"/projects/{new_name}"})


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
    log_operation("create_chapter", str(path))
    return RedirectResponse(url=f"/projects/{safe_project}/editor/{path.name}", status_code=303)

@app.delete("/projects/{project}/chapters/{filename}")
def delete_chapter(request: Request, project: str, filename: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    path = chapter_path(safe_project, filename)
    if path.exists():
        path.unlink()
        with get_conn() as conn:
            conn.execute("DELETE FROM file_index WHERE path=?", (str(path),))
        log_operation("delete_chapter", str(path))
    return JSONResponse(content={"status": "ok", "new_url": f"/projects/{safe_project}"})

@app.put("/projects/{project}/chapters/{filename}/rename")
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
            conn.execute("UPDATE file_index SET path=? WHERE path=?", (str(new_p), str(old_p)))
        log_operation("rename_chapter", f"{old_p.name} -> {new_p.name}")
        
    return JSONResponse(content={"status": "ok", "new_url": f"/projects/{safe_project}/editor/{new_p.name}"})


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
    
    active_idx = next((i for i, c in enumerate(chapters) if c["filename"] == path.name), 0)
    start_idx = max(0, active_idx - 20)
    end_idx = min(len(chapters), active_idx + 21)
    visible_chapters = chapters[start_idx:end_idx]

    proj_meta = get_project_meta(safe_project)
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
        },
    )

@app.get("/projects/{project}/sidebar_chapters")
def sidebar_chapters(request: Request, project: str, q: str = "", active: str = "") -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    chapters = list_chapters(safe_project)
    
    if q:
        q = q.lower()
        chapters = [c for c in chapters if q in c["title"].lower() or q in c.get("chapter", "").lower() or q in c.get("volume", "").lower()]
        visible = chapters[:50]
    else:
        active_idx = next((i for i, c in enumerate(chapters) if c["filename"] == active), 0)
        start_idx = max(0, active_idx - 20)
        end_idx = min(len(chapters), active_idx + 21)
        visible = chapters[start_idx:end_idx]
        
    return templates.TemplateResponse("_sidebar_chapters.html", {"request": request, "project": safe_project, "chapters": visible, "filename": active})



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
    _loaded_mtime: str = Form(""),
) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    path = chapter_path(safe_project, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="chapter not found")

    # M11: Conflict detection
    if _loaded_mtime:
        try:
            loaded_mtime = float(_loaded_mtime)
            current_mtime = path.stat().st_mtime
            if abs(current_mtime - loaded_mtime) > 0.5:
                return templates.TemplateResponse(
                    "_save_result.html",
                    {"request": request, "error": "\u6587\u4ef6\u5df2\u88ab\u5176\u4ed6\u6765\u6e90\u4fee\u6539\uff0c\u8bf7\u5237\u65b0\u9875\u9762\u540e\u91cd\u8bd5\u3002"},
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

    write_markdown(path, frontmatter, body)
    log_operation("save", str(path), f"words_added={words_added}")
    new_mtime = path.stat().st_mtime
    return templates.TemplateResponse(
        "_save_result.html",
        {"request": request, "saved_at": utc_now().strftime("%Y-%m-%d %H:%M:%S UTC"), "word_count": new_words, "new_mtime": new_mtime},
    )


@app.post("/projects/{project}/chapters/reorder")
async def reorder_chapters(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    filenames = data.get("order", [])
    
    if not filenames:
        return JSONResponse(status_code=400, content={"error": "empty order"})
    
    for new_idx, fname in enumerate(filenames, start=1):
        old_path = chapter_path(safe_project, fname)
        if not old_path.exists():
            continue
            
        fm, body = read_markdown(old_path)
        stem_parts = fname.replace(".md", "").split("-", 1)
        rest = stem_parts[1] if len(stem_parts) > 1 else stem_parts[0]
        new_name = f"{new_idx:05d}-{rest}.md"
        new_path = old_path.parent / new_name
        
        if old_path != new_path:
            old_path.rename(new_path)
            with get_conn() as conn:
                conn.execute("UPDATE file_index SET path=? WHERE path=?", (str(new_path), str(old_path)))
                conn.execute("UPDATE chapter_fts SET path=? WHERE path=?", (str(new_path), str(old_path)))
    
    log_operation("reorder_chapters", safe_project, f"reordered {len(filenames)} chapters")
    return JSONResponse(content={"status": "ok"})


@app.post("/projects/{project}/meta")
def update_project_meta(
    request: Request,
    project: str,
    target_words: int = Form(100000),
    author: str = Form(""),
    synopsis: str = Form(""),
    daily_goal: int = Form(2000),
) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    set_project_meta(safe_project, target_words, author, synopsis, daily_goal)
    log_operation("update_project_meta", safe_project)
    return RedirectResponse(url=f"/projects/{safe_project}", status_code=303)


@app.post("/projects/{project}/preview", response_class=HTMLResponse)
def preview_markdown(request: Request, body: str = Form("")) -> Response:
    require_auth(request)
    html = markdown.markdown(body, extensions=["fenced_code", "tables"])
    return templates.TemplateResponse("_preview.html", {"request": request, "html": html})


@app.post("/projects/{project}/characters/new")
def create_character(
    request: Request,
    project: str,
    name: str = Form("新角色"),
) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    filename = f"{safe_slug(name, fallback='character')}.md"
    p = project_path(safe_project) / "characters" / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        frontmatter = {"name": name, "tags": [], "role": ""}
        write_markdown(p, frontmatter, f"这是 {name} 的介绍。")
        log_operation("create_character", str(p))
    return RedirectResponse(url=f"/projects/{safe_project}/characters", status_code=303)


@app.get("/projects/{project}/characters", response_class=HTMLResponse)
def characters_page(request: Request, project: str) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    safe_project = safe_slug(project, fallback="project")
    items = list_notes(safe_project, "characters")
    return templates.TemplateResponse("characters.html", {"request": request, "project": safe_project, "items": items})


@app.post("/projects/{project}/world/new")
def create_world_item(
    request: Request,
    project: str,
    name: str = Form("新条目"),
    category: str = Form("locations"),
) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")

    prefix_map = {
        "locations": "loc",
        "organizations": "org",
        "items": "ite",
        "timeline": "tim"
    }
    prefix = prefix_map.get(category, "loc")

    filename = f"{prefix}-{safe_slug(name, fallback='world')}.md"
    p = project_path(safe_project) / "world" / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        frontmatter = {"name": name, "category": category, "tags": []}
        write_markdown(p, frontmatter, f"这是 {name} 的介绍。")
        log_operation("create_world_item", str(p))
    return RedirectResponse(url=f"/projects/{safe_project}/world", status_code=303)


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


@app.get("/projects/{project}/search", response_class=HTMLResponse)
def search_project(request: Request, project: str, q: str = "") -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    safe_project = safe_slug(project, fallback="project")
    results = []
    if q:
        with get_conn() as conn:
            # Query body or title
            rows = conn.execute(
                """
                SELECT path, title, snippet(chapter_fts, 3, '<mark class="bg-accent/30 text-accent px-1 rounded">', '</mark>', '...', 64) as excerpt
                FROM chapter_fts
                WHERE chapter_fts MATCH ? AND project = ?
                ORDER BY rank
                LIMIT 50
                """,
                (f'"{q}"', safe_project)
            ).fetchall()
            
            for r in rows:
                p = Path(r["path"])
                results.append({
                    "filename": p.name,
                    "title": r["title"],
                    "excerpt": r["excerpt"]
                })
                
    return templates.TemplateResponse("search.html", {"request": request, "project": safe_project, "q": q, "results": results})

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
