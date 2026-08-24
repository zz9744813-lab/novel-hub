"""NovelForge API main entry point.

P0-08: production shared-token auth for all /api/*
P0-09: lifespan fail-fast readiness (no background bootstrap sleep)
"""
from __future__ import annotations

import os
import uuid
import json
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router, seed_prompt_templates
from app.routers import library as library_router
from app.routers import imports as imports_router
from app.routers import prompt_studio as prompt_studio_router
from app.routers import tasks as tasks_router
from app.routers import system as system_router
from app.routers import style as style_router
from app.routers import model_center as model_center_router
from app.api import research as research_router
from app.api import editorial as editorial_router

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

    # v9.1: idempotent research source seeding (spec §18)
    try:
        from app.database import async_session_factory
        from app.research.seeding import seed_research_sources

        async with async_session_factory() as db:
            report = await seed_research_sources(db)
            await db.commit()
            logger.info(f"research sources seeded: {report}")
    except Exception as e:
        logger.warning(f"seed_research_sources skipped: {e}")

    # Model bindings are an approval-controlled production input. Bootstrap is
    # available only as an explicit non-production opt-in, never during prod boot.
    if (
        not _is_production()
        and os.environ.get("ALLOW_AUTO_BINDING_BOOTSTRAP", "").lower() == "true"
    ):
        try:
            await ensure_required_bindings()
        except Exception as e:
            logger.error(f"ensure_required_bindings failed: {e}")
    else:
        logger.info("Skipping automatic model binding bootstrap")

    from app.startup_checks import run_all_checks

    ok, report = await run_all_checks()
    _READY_DETAIL = report
    _READY = ok
    if not ok:
        logger.error(f"Readiness FAILED: {report}")
    else:
        logger.info(f"Readiness OK: {report}")

    # P1 CORE-005: clean orphan AgentRuns on API boot (no redis enqueue)
    try:
        from app.engine.reconciler import reconcile_orphan_agent_runs

        rec = await reconcile_orphan_agent_runs()
        logger.info(f"API startup orphan reconciler: {rec}")
    except Exception as e:
        logger.warning(f"API orphan reconciler skipped: {e}")

    global _event_task
    _event_task = asyncio.create_task(_event_listener())
    yield
    if _event_task:
        _event_task.cancel()
        try:
            await _event_task
        except asyncio.CancelledError:
            pass
        _event_task = None
    _READY = False


app = FastAPI(
    title="NovelForge",
    description="NovelForge v8.0 — 书架 / 企划导入 / 提示词工坊",
    version="8.0.0",
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
        if path.startswith("/api") or path in ("/docs", "/redoc", "/openapi.json"):
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
app.include_router(library_router.router)
app.include_router(imports_router.router)
app.include_router(prompt_studio_router.router)
app.include_router(tasks_router.router)
app.include_router(system_router.router)
app.include_router(style_router.router)
app.include_router(model_center_router.router)
app.include_router(research_router.router)
app.include_router(editorial_router.router)


# WebSocket connection manager for real-time events
_ws_connections: dict[str, list[WebSocket]] = {}
_event_task: asyncio.Task | None = None


async def _event_listener():
    """Bridge Redis pub/sub events from workers into connected WebSockets."""
    redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe("novelforge:events")
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                payload = json.loads(message["data"])
                await broadcast_event(payload.pop("type", "event"), payload)
            except Exception:
                logger.warning("invalid realtime event", exc_info=True)
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await pubsub.unsubscribe("novelforge:events")
            await pubsub.close()
            await redis.aclose()
        except Exception:
            pass



@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """WebSocket endpoint for real-time chapter/import events.

    Auth is done after accept via the first JSON frame so the long-lived admin
    token never lands in query strings, access logs, or browser history.
    Expected first message: {"type":"auth","token":"<ADMIN_API_TOKEN>"}.
    """
    configured_token = (os.environ.get("ADMIN_API_TOKEN") or "").strip()
    await websocket.accept()
    conn_id = uuid.uuid4().hex
    authenticated = not (configured_token and not configured_token.startswith("replace-"))

    try:
        if not authenticated:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
                payload = json.loads(raw)
            except Exception:
                await websocket.close(code=1008, reason="unauthorized")
                return
            supplied = ""
            if isinstance(payload, dict):
                if payload.get("type") == "auth":
                    supplied = str(payload.get("token") or "").strip()
                else:
                    supplied = str(payload.get("token") or payload.get("Authorization") or "").strip()
                    if supplied.lower().startswith("bearer "):
                        supplied = supplied[7:].strip()
            if supplied != configured_token:
                await websocket.close(code=1008, reason="unauthorized")
                return
            authenticated = True

        if "events" not in _ws_connections:
            _ws_connections["events"] = []
        _ws_connections["events"].append(websocket)

        await websocket.send_json({"type": "connected", "conn_id": conn_id})

        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
                continue
            try:
                msg = json.loads(data)
            except Exception:
                continue
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in _ws_connections.get("events", []):
            _ws_connections["events"].remove(websocket)


async def broadcast_event(event_type: str, payload: dict):
    """Broadcast event to all connected WebSocket clients."""
    message = {"type": event_type, **payload}
    for ws in list(_ws_connections.get("events", [])):
        try:
            await ws.send_json(message)
        except Exception:
            if ws in _ws_connections.get("events", []):
                _ws_connections["events"].remove(ws)


async def _broadcast_chapter_event(chapter_id: uuid.UUID, event_type: str, detail: dict):
    """Broadcast chapter status change via WebSocket."""
    await broadcast_event(event_type, {"chapter_id": str(chapter_id), **detail})


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
