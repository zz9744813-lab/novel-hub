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
    """
    import asyncio
    await asyncio.sleep(2)  # Let DB be ready
    from app.database import async_session_factory
    from app.v74_utils import ModelBindingService
    from app.prompts import AGENT_MODELS
    from app.config import settings
    
    async with async_session_factory() as db:
        svc = ModelBindingService(db)
        # Check if any global bindings exist
        from app.models.tables import AgentModelBinding
        from sqlalchemy import select
        result = await db.execute(
            select(AgentModelBinding).where(AgentModelBinding.scope_type == "global")
        )
        existing = result.scalars().all()
        if existing:
            return  # Already bootstrapped
        
        # Create global bindings from .env defaults
        for agent_role, model_name in AGENT_MODELS.items():
            provider = "openrouter"  # Default provider
            await svc.get_or_create_binding(
                agent_role=agent_role,
                provider=provider,
                primary_model=model_name,
                fallback_model=None,
                reasoning_mode="auto",
                updated_by="system_bootstrap",
            )
        await db.commit()
        print("[v7.4] Model bindings bootstrapped from .env")


@app.on_event("shutdown")
async def shutdown():
    pass
