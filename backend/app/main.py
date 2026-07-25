"""NovelForge API main entry point."""
import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.api.routes import router, seed_prompt_templates
from app.config import settings

app = FastAPI(
    title="NovelForge",
    description="全自动超长篇小说写作系统 v7.4",
    version="7.4.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS: prefer ADMIN_CORS_ORIGINS comma list; default * for private VPS cockpit
_cors = os.environ.get("ADMIN_CORS_ORIGINS", "*").strip()
_origins = ["*"] if _cors == "*" else [o.strip() for o in _cors.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple shared-token gate for mutating admin/write endpoints when ADMIN_API_TOKEN is set.
# Read-only GET/HEAD/OPTIONS stay open for cockpit UI; health always open.
_PROTECTED_PREFIXES = (
    "/api/model-bindings",
    "/api/admin",
    "/api/books",
    "/api/genre-profiles",
    "/api/research-sessions",
    "/api/chapters",
)


@app.middleware("http")
async def admin_token_middleware(request: Request, call_next):
    token = os.environ.get("ADMIN_API_TOKEN", "").strip()
    if not token:
        return await call_next(request)

    path = request.url.path
    method = request.method.upper()
    if method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    if path.startswith("/health") or path in ("/docs", "/redoc", "/openapi.json"):
        return await call_next(request)

    needs = any(path.startswith(p) for p in _PROTECTED_PREFIXES)
    if needs:
        provided = request.headers.get("X-Admin-Token") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if provided != token:
            return JSONResponse(status_code=401, content={"detail": "admin token required"})
    return await call_next(request)


app.include_router(router)


@app.on_event("startup")
async def startup():
    asyncio.create_task(seed_prompt_templates())
    # v7.4: Bootstrap agent model bindings from .env on first startup
    asyncio.create_task(bootstrap_model_bindings())


async def bootstrap_model_bindings():
    """C-21: Initialize model bindings from .env on first startup.
    After this, .env is not read for model config at runtime.

    C-23: Ensure draft_writer and other strict roles exist; log hard error if missing
    after bootstrap. Provider defaults to PRIMARY provider label, not hard-coded openrouter
    when NEW_API is the configured primary.
    """
    import asyncio
    await asyncio.sleep(2)  # Let DB be ready
    from app.database import async_session_factory
    from app.v74_utils import ModelBindingService
    from app.prompts import AGENT_MODELS
    from app.models.tables import AgentModelBinding
    from sqlalchemy import select

    # Prefer explicit provider name; when PRIMARY points at new-api, label it new-api
    primary_base = os.environ.get("PRIMARY_BASE_URL", "")
    if "new-api" in primary_base or primary_base.endswith(":3000/v1"):
        default_provider = "new-api"
    else:
        default_provider = os.environ.get("DEFAULT_PROVIDER", "openrouter")

    # Include aileak_judge even if not in AGENT_MODELS prompts map
    roles = dict(AGENT_MODELS)
    roles.setdefault("aileak_judge", roles.get("review_agent", "deepseek-v4-flash"))

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
                updated_by="system_bootstrap",
            )
            created += 1
        if created:
            await db.commit()
            print(f"[v7.4] Model bindings bootstrapped: +{created} roles, provider={default_provider}")

        # C-23: verify strict production roles exist
        result = await db.execute(
            select(AgentModelBinding).where(AgentModelBinding.scope_type == "global")
        )
        have = {b.agent_role for b in result.scalars().all()}
        required = {
            "draft_writer", "chapter_planner", "review_agent",
            "state_extractor", "outline_parser",
        }
        missing = required - have
        if missing:
            print(f"[v7.4][FATAL] Missing required model bindings: {sorted(missing)}")
        else:
            print(f"[v7.4] Required model bindings OK: {sorted(required)}")


@app.on_event("shutdown")
async def shutdown():
    pass
