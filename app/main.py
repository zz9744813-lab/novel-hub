from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from starlette_csrf import CSRFMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.responses import Response, JSONResponse
from fastapi.staticfiles import StaticFiles

from starlette.middleware.sessions import SessionMiddleware

from app.config import (
    BASE_DIR, NOVELS_ROOT, BACKUP_ROOT, SECRET_KEY, APP_ENV,
    env_bool, FEATURES, feature_enabled, validate_runtime_config,
)
from app.db import get_conn, get_setting, set_setting
from app.security import require_auth
from app.labels import status_label
from app.templating import create_templates
from app.services.markdown_service import read_markdown, safe_slug, parse_frontmatter, write_atomic, count_words
from app.services.path_service import chapter_path
from app.services.snapshot_service import backup_file
from app.services.project_service import set_project_meta
from app.services.chapter_service import write_markdown
from app.services.library_service import _reindex_notes_for_project, list_chapters, list_notes, scan_projects
from app.services.metrics_service import log_operation, compute_trend, get_project_stats
from app.schema import init_db
from app.routers import health as health_router
from app.routers.auth import create_router as create_auth_router
from app.routers import settings as settings_router
from app.routers import dashboard as dashboard_router
from app.routers import projects as projects_router
from app.routers import chapters as chapters_router
from app.routers import editor as editor_router
from app.routers import snapshots as snapshots_router
from app.routers import export as export_router
from app.routers import notes as notes_router
from app.routers import volumes as volumes_router
from app.routers import entities as entities_router
from app.routers import scenes as scenes_router
from app.routers import workflow as workflow_router
from app.routers import ai as ai_router
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
app.include_router(editor_router.router)
app.include_router(snapshots_router.router)
app.include_router(export_router.router)
app.include_router(notes_router.router)
app.include_router(volumes_router.router)
app.include_router(entities_router.router)
app.include_router(scenes_router.router)
app.include_router(workflow_router.router)
app.include_router(ai_router.router)

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


