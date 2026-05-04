from __future__ import annotations

import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import json
import gzip
import hashlib
from typing import Any, List, Dict, Tuple

import markdown
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.services.ai_client import generate_ai_content
from app.services.wiki_link import update_entity_refs
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
        accept = request.headers.get("accept", "")
        if request.headers.get("hx-request") == "true":
            raise HTTPException(status_code=401, headers={"HX-Redirect": "/login"})
        if "text/html" in accept:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
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
                kind UNINDEXED,
                title,
                body,
                tokenize='unicode61 remove_diacritics 2'
            );

            -- C-Route (v6) Schema
            CREATE TABLE IF NOT EXISTS entities (
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                aliases TEXT,
                md_path TEXT,
                properties TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_entities_project_kind ON entities(project, kind);
            CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(project, name);

            CREATE TABLE IF NOT EXISTS entity_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                notes TEXT,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_rel_source ON entity_relations(source_id);
            CREATE INDEX IF NOT EXISTS idx_rel_target ON entity_relations(target_id);

            CREATE TABLE IF NOT EXISTS scenes (
                id TEXT PRIMARY KEY,
                chapter_path TEXT NOT NULL,
                project TEXT NOT NULL,
                seq INTEGER NOT NULL,
                title TEXT,
                pov TEXT,
                location_id TEXT,
                word_count INTEGER,
                char_offset_start INTEGER,
                char_offset_end INTEGER,
                summary TEXT,
                status TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_scenes_chapter ON scenes(chapter_path, seq);

            CREATE TABLE IF NOT EXISTS entity_refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_path TEXT NOT NULL,
                scene_id TEXT,
                entity_id TEXT NOT NULL,
                ref_kind TEXT,
                char_offset INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_refs_entity ON entity_refs(entity_id);
            CREATE INDEX IF NOT EXISTS idx_refs_chapter ON entity_refs(chapter_path);

            CREATE TABLE IF NOT EXISTS volumes (
                project TEXT NOT NULL,
                slug TEXT NOT NULL,
                title TEXT,
                seq INTEGER,
                synopsis TEXT,
                target_words INTEGER,
                PRIMARY KEY (project, slug)
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                label TEXT,
                content_hash TEXT,
                content BLOB
            );
            CREATE INDEX IF NOT EXISTS idx_snapshots_chapter ON snapshots(chapter_path, created_at);
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
        try:
            conn.execute("ALTER TABLE operation_logs ADD COLUMN project TEXT")
        except: pass
        try:
            conn.execute("ALTER TABLE operation_logs ADD COLUMN value INTEGER DEFAULT 0")
        except: pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_time_action ON operation_logs(created_at, action)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_target ON operation_logs(target)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_project ON operation_logs(project)")

        # Migrate chapter_fts to add 'kind' column (FTS5 doesn't support ALTER)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(chapter_fts)").fetchall()]
            if cols and "kind" not in cols:
                conn.execute("DROP TABLE chapter_fts")
                conn.execute("""
                    CREATE VIRTUAL TABLE chapter_fts USING fts5(
                        path UNINDEXED, project UNINDEXED, kind UNINDEXED,
                        title, body, tokenize='unicode61 remove_diacritics 2'
                    )
                """)
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    ("fts_needs_rebuild", "1"),
                )
        except sqlite3.OperationalError:
            pass


def _reindex_notes_for_project(project: str) -> None:
    """Re-populate chapter_fts for character/world/hooks notes. Used after FTS schema migration."""
    proj_dir = NOVELS_ROOT / project
    if not proj_dir.exists():
        return
    with get_conn() as conn:
        for sub in ("characters", "world", "hooks"):
            sub_dir = proj_dir / sub
            if not sub_dir.exists():
                continue
            for f in sub_dir.glob("*.md"):
                try:
                    fm, body = read_markdown(f)
                    title = fm.get("title", f.stem) if fm else f.stem
                    conn.execute("DELETE FROM chapter_fts WHERE path=?", (str(f),))
                    conn.execute(
                        "INSERT INTO chapter_fts(path, project, kind, title, body) VALUES (?, ?, ?, ?, ?)",
                        (str(f), project, _infer_kind(f), title, body),
                    )
                except Exception:
                    pass


def log_operation(action: str, target: str = "", detail: str = "", value: int = 0, project: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO operation_logs(action, target, project, created_at, detail, value) VALUES (?, ?, ?, ?, ?, ?)",
            (action, target, project, utc_now().isoformat(), detail, value),
        )


def _infer_kind(p: Path) -> str:
    parts = p.parts
    if "chapters" in parts: return "chapter"
    if "hooks" in parts: return "hook"
    if "characters" in parts: return "character"
    if "world" in parts: return "world"
    return "other"


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


def backup_file(path: Path, label: str = "auto") -> None:
    p = _ensure_under_root(path, VAULT_ROOT)
    if not p.exists():
        return
    content = p.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    
    with get_conn() as conn:
        # Deduplication: check if last snapshot for this file has same hash
        last = conn.execute(
            "SELECT content_hash FROM snapshots WHERE chapter_path = ? ORDER BY created_at DESC LIMIT 1",
            (str(p),)
        ).fetchone()
        
        if last and last["content_hash"] == content_hash:
            return # No change
            
        compressed = gzip.compress(content.encode("utf-8"))
        conn.execute(
            "INSERT INTO snapshots(chapter_path, created_at, label, content_hash, content) VALUES (?, ?, ?, ?, ?)",
            (str(p), utc_now().isoformat(), label, content_hash, compressed)
        )
        # Cleanup policy: Keep last 50
        conn.execute(
            """DELETE FROM snapshots WHERE id IN (
                SELECT id FROM snapshots WHERE chapter_path = ? 
                ORDER BY created_at DESC LIMIT -1 OFFSET 50
            )""",
            (str(p),)
        )


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


def normalize_meta(fm: dict[str, Any], stem: str, project: str = None) -> dict[str, Any]:
    def resolve_entities(items, kind):
        if not items: return []
        if not project: return items
        resolved = []
        with get_conn() as conn:
            for item in items:
                if isinstance(item, str):
                    # Try to bind ID by name
                    row = conn.execute("SELECT id FROM entities WHERE project=? AND name=?", (project, item)).fetchone()
                    if row:
                        resolved.append({"id": row["id"], "name": item})
                    else:
                        resolved.append({"id": None, "name": item}) # Unbound
                else:
                    resolved.append(item)
        return resolved

    res = {
        "title": fm.get("title", stem),
        "chapter": fm.get("chapter", ""),
        "status": fm.get("status", "draft"),
        "volume": fm.get("volume", ""),
        "tags": fm.get("tags", []),
        "synopsis": fm.get("synopsis", ""),
        "notes": fm.get("notes", ""),
        "pov": fm.get("pov", ""),
        "characters": resolve_entities(fm.get("characters", []), "character"),
        "locations": resolve_entities(fm.get("locations", []), "location"),
        "warnings": fm.get("warnings", []),
        "draft_version": fm.get("draft_version", ""),
    }
    return res


def list_chapters(project: str, sync: bool = False) -> list[dict[str, Any]]:
    folder = project_path(project) / "chapters"
    rows: list[dict[str, Any]] = []
    with get_conn() as conn:
        if sync:
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
                    meta = normalize_meta(fm, f.stem, project=project)
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
                    conn.execute("INSERT INTO chapter_fts(path, project, kind, title, body) VALUES (?, ?, ?, ?, ?)", 
                                 (f_str, project, _infer_kind(f), meta["title"], body))

        db_rows = conn.execute(
            "SELECT path, word_count, updated_at, title, chapter, chapter_int, status, volume, mtime "
            "FROM file_index WHERE project=? ORDER BY chapter_int, path",
            (project,)
        ).fetchall()
        
        for r in db_rows:
            f = Path(r["path"])
            rows.append({
                "filename": f.name,
                "title": r["title"],
                "chapter": r["chapter"],
                "status": r["status"],
                "volume": r["volume"],
                "word_count": r["word_count"],
                "modified": datetime.fromtimestamp(r["mtime"], tz=timezone.utc),
                "meta": {
                    "title": r["title"],
                    "chapter": r["chapter"],
                    "status": r["status"],
                    "volume": r["volume"],
                }
            })
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
                "daily_goal": meta.get("daily_goal", DAILY_GOAL_WORDS),
            }
        )
    return results


def _project_from_path(p: Path) -> str:
    """Walk path parts looking for the 'Novels' segment; project is whatever follows it."""
    try:
        idx = p.parts.index("Novels")
        return p.parts[idx + 1] if idx + 1 < len(p.parts) else ""
    except ValueError:
        return ""


def write_markdown(path: Path, frontmatter: dict[str, Any], body: str, project: str = None) -> None:
    p = _ensure_under_root(path, VAULT_ROOT)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        backup_file(p)
    content = dump_frontmatter(frontmatter, body)
    p.write_text(content, encoding="utf-8")

    if project is None:
        project = _project_from_path(p)

    # T1.3: Convert strings to objects in frontmatter for "new format" writing
    meta = normalize_meta(frontmatter, p.stem, project=project)
    frontmatter["characters"] = meta["characters"]
    frontmatter["locations"] = meta["locations"]
    
    # Write file first
    content = dump_frontmatter(frontmatter, body)
    p.write_text(content, encoding="utf-8")

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
            (str(p), project, words, utc_now().isoformat(), meta["title"], meta["chapter"], chapter_int, meta["status"], meta["volume"], mtime),
        )
        conn.execute("DELETE FROM chapter_fts WHERE path=?", (str(p),))
        conn.execute(
            "INSERT INTO chapter_fts(path, project, kind, title, body) VALUES (?, ?, ?, ?, ?)",
            (str(p), project, _infer_kind(p), meta["title"], body),
        )

        # T1.4: Scenes Indexing
        conn.execute("DELETE FROM scenes WHERE chapter_path=?", (str(p),))
        scene_matches = list(re.finditer(r"^##\s+(.*)$", body, re.MULTILINE))
        if not scene_matches:
            # Whole chapter as one scene
            sc_id = f"sc_{hashlib.sha1((str(p) + '1').encode()).hexdigest()[:8]}"
            conn.execute(
                "INSERT INTO scenes(id, chapter_path, project, seq, title, word_count, char_offset_start, char_offset_end) VALUES (?,?,?,?,?,?,?,?)",
                (sc_id, str(p), project, 1, meta["title"], words, 0, len(body))
            )
        else:
            for i, match in enumerate(scene_matches):
                title = match.group(1).strip()
                start = match.end()
                end = scene_matches[i+1].start() if i + 1 < len(scene_matches) else len(body)
                scene_body = body[start:end]
                sc_id = f"sc_{hashlib.sha1((str(p) + str(i+1)).encode()).hexdigest()[:8]}"
                conn.execute(
                    "INSERT INTO scenes(id, chapter_path, project, seq, title, word_count, char_offset_start, char_offset_end) VALUES (?,?,?,?,?,?,?,?)",
                    (sc_id, str(p), project, i+1, title, count_words(scene_body), start, end)
                )

    # T1.2: Wiki Link Refs
    update_entity_refs(str(p), body, project)


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
    needs_note_rebuild = get_setting("fts_needs_rebuild", "0") == "1"
    for p in [item.name for item in NOVELS_ROOT.iterdir() if item.is_dir()]:
        list_chapters(p, sync=True)
        if needs_note_rebuild:
            _reindex_notes_for_project(p)
    if needs_note_rebuild:
        set_setting("fts_needs_rebuild", "0")


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
            conn.execute("DELETE FROM chapter_fts WHERE project=?", (safe_project,))
            conn.execute("DELETE FROM project_meta WHERE project=?", (safe_project,))
        log_operation("delete_project", safe_project, project=safe_project)
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
                new_path_str = old_path.replace(str(old_p), str(new_p), 1)
                conn.execute("UPDATE file_index SET path=?, project=? WHERE path=?", (new_path_str, new_name, old_path))
                conn.execute("UPDATE chapter_fts SET path=?, project=? WHERE path=?", (new_path_str, new_name, old_path))
                
            conn.execute("UPDATE ai_pipelines SET project=? WHERE project=?", (new_name, safe_project))
            conn.execute("UPDATE project_meta SET project=? WHERE project=?", (new_name, safe_project))
        log_operation("rename_project", f"{project} -> {new_name}", project=new_name)
        
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
    log_operation("create_chapter", str(path), project=safe_project)
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
            conn.execute("DELETE FROM chapter_fts WHERE path=?", (str(path),))
        log_operation("delete_chapter", str(path), project=safe_project)
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
            conn.execute("UPDATE chapter_fts SET path=? WHERE path=?", (str(new_p), str(old_p)))
        log_operation("rename_chapter", f"{filename} -> {new_p.name}", project=safe_project)
        
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
        
        # Open threads (kind='thread', status='open' in properties JSON)
        threads = conn.execute(
            "SELECT * FROM entities WHERE project = ? AND kind = 'thread' AND properties LIKE '%\"status\": \"open\"%'", 
            (safe_project,)).fetchall()
            
        snapshots = conn.execute(
            "SELECT id, created_at, label FROM snapshots WHERE chapter_path = ? ORDER BY created_at DESC LIMIT 50",
            (str(path),)).fetchall()

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
            "open_threads": [dict(t) for t in threads],
            "snapshots": [dict(s) for s in snapshots]
        },
    )

@app.get("/projects/{project}/sidebar_chapters")
def sidebar_chapters(request: Request, project: str, q: str = "", active: str = "") -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    # Project detail triggers index sync
    chapters = list_chapters(safe_project, sync=True)
    
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
    log_operation("save", str(path), f"words_added={words_added}", value=words_added, project=safe_project)
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
        
    log_operation("reorder_chapters", safe_project, f"reordered {len(filenames)} chapters", project=safe_project)
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
    return RedirectResponse(url=f"/projects/{project}/entities?kind=character", status_code=301)


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
    return RedirectResponse(url=f"/projects/{project}/entities?kind=world", status_code=301)
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
        escaped = q.replace('"', '""')
        with get_conn() as conn:
            # Query body or title
            rows = conn.execute(
                """
                SELECT path, title, snippet(chapter_fts, 4, '<mark class="bg-accent/20 text-accent px-0.5 rounded">', '</mark>', '...', 64) as excerpt
                FROM chapter_fts
                WHERE chapter_fts MATCH ? AND project = ?
                ORDER BY rank LIMIT 50
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

@app.get("/projects/{project}/stats", response_class=HTMLResponse)
def project_stats(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    with get_conn() as conn:
        # Aggregate words_added from operation_logs
        # Detail format: "words_added=123"
        rows = conn.execute(
            """
            SELECT date(created_at) as day, sum(value) as words
            FROM operation_logs
            WHERE action='save' AND project=?
            GROUP BY day
            ORDER BY day DESC
            LIMIT 90
            """,
            (safe_project,)
        ).fetchall()
    
    stats = [{"day": r["day"], "words": r["words"]} for r in rows]
    return templates.TemplateResponse("stats.html", {"request": request, "project": safe_project, "stats": stats})


@app.get("/projects/{project}/export/all")
def export_project_combined(request: Request, project: str):
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    chapters = list_chapters(safe_project)
    
    combined = [f"# {project}\n\n"]
    for c in chapters:
        path = chapter_path(safe_project, c["filename"])
        if path.exists():
            fm, body = read_markdown(path)
            combined.append(f"## {fm.get('title', c['filename'])}\n\n")
            combined.append(body)
            combined.append("\n\n---\n\n")
            
    content = "".join(combined)
    return Response(
        content=content,
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename={safe_project}_combined.md"}
    )


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


@app.get("/projects/{project}/hooks", response_class=HTMLResponse)
def hooks_page(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    items = list_notes(safe_project, "hooks")
    return templates.TemplateResponse("hooks.html", {"request": request, "project": safe_project, "items": items})


@app.post("/projects/{project}/hooks/new")
def create_hook(request: Request, project: str, name: str = Form("")) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    folder = project_path(safe_project) / "hooks"
    folder.mkdir(parents=True, exist_ok=True)
    
    filename = f"{safe_slug(name, fallback='hook')}.md"
    path = folder / filename
    write_markdown(path, {"title": name, "status": "open"}, f"# {name}\n\n在这里记录待填的坑或伏笔...")
    return RedirectResponse(url=f"/projects/{safe_project}/hooks", status_code=303)


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


@app.post("/settings/reindex")
def reindex_all(request: Request) -> Response:
    require_auth(request)
    if NOVELS_ROOT.exists():
        for p in NOVELS_ROOT.iterdir():
            if p.is_dir():
                list_chapters(p.name, sync=True)
    log_operation("reindex_all")
    return RedirectResponse(url="/settings", status_code=303)


# --- C-Route (v6) API Routes ---

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
    return JSONResponse(content={"status": "ok", "id": ent_id})

@app.put("/api/entities/{ent_id}")
async def api_update_entity(request: Request, ent_id: str) -> Response:
    require_auth(request)
    data = await request.json()
    with get_conn() as conn:
        conn.execute(
            """UPDATE entities SET 
               name=?, aliases=?, properties=?, updated_at=?
               WHERE id=?""",
            (data["name"], json.dumps(data.get("aliases", [])), json.dumps(data.get("properties", {})), utc_now().isoformat(), ent_id)
        )
    return JSONResponse(content={"status": "ok"})

@app.delete("/api/entities/{ent_id}")
def api_delete_entity(request: Request, ent_id: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        conn.execute("DELETE FROM entities WHERE id=?", (ent_id,))
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


@app.post("/api/projects/{project}/bulk-bind-entities")
def api_bulk_bind(request: Request, project: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        entities = conn.execute("SELECT id, name FROM entities WHERE project=?", (project,)).fetchall()
        name_map = {e["name"]: e["id"] for e in entities}
        
        chapters = conn.execute("SELECT path FROM file_index WHERE project=?", (project,)).fetchall()
        updated_count = 0
        for ch in chapters:
            path = Path(ch["path"])
            if not path.exists(): continue
            fm, body = read_markdown(path)
            
            # Regex to find [[name]] but NOT [[id|name]]
            # Negative lookahead for | or ] (simple version)
            new_body = re.sub(r"\[\[([^|\]#]+)\]\]", lambda m: f"[[{name_map[m.group(1)]}|{m.group(1)}]]" if m.group(1) in name_map else m.group(0), body)
            
            if new_body != body:
                write_markdown(path, fm, new_body, project=project)
                updated_count += 1
                
        return JSONResponse(content={"status": "ok", "updated_chapters": updated_count})


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
        
        return templates.TemplateResponse(
            "entity_detail.html",
            {
                "request": request,
                "project": project,
                "entity": dict(entity),
                "entity_aliases": json.loads(entity["aliases"] or "[]"),
                "md_content": md_content,
                "relations": [dict(r) for r in relations],
                "appearances": [dict(a) for a in appearances]
            }
        )


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
