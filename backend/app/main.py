"""NovelForge API main entry point.

P0-08: production shared-token auth for all /api/*
P0-09: lifespan fail-fast readiness (no background bootstrap sleep)
"""
from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router, seed_prompt_templates
from app.config import settings

logger = logging.getLogger("novelforge.main")

# Shared readiness flag for health endpoints
_READY = False
_READY_DETAIL: dict = {}


def get_readiness() -> tuple[bool, dict]:
    return _READY, _READY_DETAIL


def _is_production() -> bool:
    return (os.environ.get("APP_ENV") or "").lower() in {"production", "prod"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _READY, _READY_DETAIL

    # Production requires admin token
    token = (os.environ.get("ADMIN_API_TOKEN") or "").strip()
    if _is_production() and (not token or token.startswith("replace-")):
        logger.error("ADMIN_API_TOKEN required in production")
        _READY = False
        _READY_DETAIL = {"error": "ADMIN_API_TOKEN missing"}
        # Still yield so live probe works; ready returns 503
        yield
        return

    # Seed prompts (idempotent) — short session
    try:
        await seed_prompt_templates()
    except Exception as e:
        logger.warning(f"seed_prompt_templates: {e}")

    # Ensure required bindings exist (explicit install step, not silent env defaults forever)
    try:
        await ensure_required_bindings()
    except Exception as e:
        logger.error(f"ensure_required_bindings failed: {e}")

    from app.startup_checks import run_all_checks

    ok, report = await run_all_checks()
    _READY_DETAIL = report
    _READY = ok
    if not ok:
        logger.error(f"Readiness FAILED: {report}")
    else:
        logger.info(f"Readiness OK: {report}")
    yield
    _READY = False


app = FastAPI(
    title="NovelForge",
    description="全自动超长篇小说写作系统 v7.4",
    version="7.4.0",
    docs_url=None if _is_production() else "/docs",
    redoc_url=None if _is_production() else "/redoc",
    openapi_url=None if _is_production() else "/openapi.json",
    lifespan=lifespan,
)

_cors = os.environ.get("ADMIN_CORS_ORIGINS", "").strip()
if _is_production():
    _origins = [o.strip() for o in _cors.split(",") if o.strip()] or [
        "http://127.0.0.1",
        "http://localhost",
        "http://107.172.138.14",
    ]
else:
    _origins = [o.strip() for o in _cors.split(",") if o.strip()] if _cors and _cors != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False if "*" in _origins else True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def admin_token_middleware(request: Request, call_next):
    """P0-08: all /api and /ws require Bearer token in production / when token set."""
    token = (os.environ.get("ADMIN_API_TOKEN") or "").strip()
    path = request.url.path
    method = request.method.upper()

    if path.startswith("/health"):
        return await call_next(request)
    if method == "OPTIONS":
        return await call_next(request)

    # When token configured (always in prod), protect API/WS/docs
    if token and not token.startswith("replace-"):
        if path.startswith("/api") or path.startswith("/ws") or path in ("/docs", "/redoc", "/openapi.json"):
            auth = request.headers.get("Authorization", "")
            provided = ""
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip()
            provided = provided or request.headers.get("X-Admin-Token", "").strip()
            if provided != token:
                logger.warning("auth_failed path=%s method=%s", path, method)
                return JSONResponse(status_code=401, content={"detail": "unauthorized"})

    return await call_next(request)


app.include_router(router)


@app.get("/health/live")
async def health_live():
    return {"status": "live"}


@app.get("/health/ready")
async def health_ready():
    if not _READY:
        return JSONResponse(status_code=503, content={"status": "not_ready", "detail": _READY_DETAIL})
    return {"status": "ready", "detail": _READY_DETAIL}


@app.get("/health")
async def health_compat():
    # backward compat for older probes
    if not _READY:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ok"}


async def ensure_required_bindings():
    """Install missing required global bindings from env model names once."""
    import os
    from sqlalchemy import select
    from app.database import async_session_factory
    from app.v74_utils import ModelBindingService
    from app.prompts import AGENT_MODELS
    from app.models.tables import AgentModelBinding
    from app.startup_checks import REQUIRED_ROLES

    primary_base = os.environ.get("PRIMARY_BASE_URL", "")
    if "new-api" in primary_base or primary_base.endswith(":3000/v1"):
        default_provider = "new-api"
    else:
        default_provider = os.environ.get("DEFAULT_PROVIDER", "openrouter")

    default_model = (
        os.environ.get("WRITER_MODEL")
        or os.environ.get("PLANNER_MODEL")
        or "deepseek-v4-flash"
    )

    roles = dict(AGENT_MODELS)
    for role in REQUIRED_ROLES:
        roles.setdefault(role, default_model)

    async with async_session_factory() as db:
        svc = ModelBindingService(db)
        result = await db.execute(
            select(AgentModelBinding).where(AgentModelBinding.scope_type == "global")
        )
        existing = {b.agent_role: b for b in result.scalars().all()}
        created = 0
        for agent_role, model_name in roles.items():
            if agent_role in existing:
                continue
            await svc.get_or_create_binding(
                agent_role=agent_role,
                provider=default_provider,
                primary_model=model_name,
                fallback_model=None,
                reasoning_mode="auto",
                updated_by="system_install",
            )
            created += 1
        if created:
            await db.commit()
            logger.info("Installed %s missing model bindings", created)
