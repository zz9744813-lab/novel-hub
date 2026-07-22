"""NovelForge API main entry point."""
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router, seed_prompt_templates
from app.config import settings

app = FastAPI(title="NovelForge", description="全自动超长篇小说写作系统 v7.3",
              version="7.3.0", docs_url="/docs", redoc_url="/redoc")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.on_event("startup")
async def startup():
    asyncio.create_task(seed_prompt_templates())


@app.on_event("shutdown")
async def shutdown():
    pass
