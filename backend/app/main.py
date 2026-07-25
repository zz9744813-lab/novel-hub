"""NovelForge API main entry point."""
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router, seed_prompt_templates
from app.config import settings

app = FastAPI(title="NovelForge", description="全自动超长篇小说写作系统 v7.4",
              version="7.4.0", docs_url="/docs", redoc_url="/redoc")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
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
    import os
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
