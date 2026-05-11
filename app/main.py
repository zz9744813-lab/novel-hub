from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.sessions import SessionMiddleware
from starlette_csrf import CSRFMiddleware

from app.config import (
    APP_ENV, BACKUP_ROOT, BASE_DIR, FEATURES, NOVELS_ROOT, SECRET_KEY,
    env_bool, feature_enabled, validate_runtime_config,
)
from app.constants import STATUS_ORDER
from app.db import get_conn, get_setting, set_setting
from app.labels import status_label
from app.schema import init_db
from app.services.chapter_service import write_markdown
from app.services.library_service import (
    _reindex_notes_for_project, list_chapters, list_notes, scan_projects,
)
from app.services.markdown_service import count_words, read_markdown, safe_slug, write_atomic
from app.services.metrics_service import compute_trend, get_project_stats, log_operation
from app.services.path_service import chapter_path
from app.services.project_service import set_project_meta
from app.services.snapshot_service import backup_file
from app.templating import create_templates
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
from app.routers import timeline as timeline_router
from app.routers import graph as graph_router
from app.routers import threads as threads_router
from app.deps import configure_runtime
from app.workflow_globals import (
    register_workflow_globals,
    project_stage_status_map,
    project_next_stage,
)

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

import re
if ENABLE_CSRF:
    app.add_middleware(
        CSRFMiddleware,
        secret=SECRET_KEY,
        exempt_urls=[re.compile(r"^/api/.*"), re.compile(r"^/login$")],
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
app.include_router(timeline_router.router)
app.include_router(graph_router.router)
app.include_router(threads_router.router)

class CacheStaticFiles(StaticFiles):
    def is_not_modified(self, response_headers, request_headers) -> bool:
        response_headers["Cache-Control"] = "public, max-age=31536000"
        return super().is_not_modified(response_headers, request_headers)

app.mount("/static", CacheStaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = create_templates()

configure_runtime(templates_obj=templates, limiter_obj=limiter)
register_workflow_globals(templates)
