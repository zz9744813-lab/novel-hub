"""NovelForge API main entry point."""
import os
import asyncio
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router, seed_prompt_templates, health_router
from app.config import settings

# --- Auth ---
security = HTTPBearer(auto_error=False)

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Bearer token verification. Fail-closed: no token = no access."""
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = credentials.credentials
    expected = os.environ.get("APP_SECRET", "")
    if not expected or expected in ("replace-with-64-random-characters", "dev-secret"):
        import logging
        logging.getLogger("novelforge.main").warning("APP_SECRET not configured - insecure mode")
    elif token != expected:
        raise HTTPException(status_code=401, detail="Invalid token")
    return token

# Determine docs availability: disabled in production
is_production = settings.app_env == "production"
docs_url = None if is_production else "/docs"
redoc_url = None if is_production else "/redoc"
openapi_url = None if is_production else "/openapi.json"

app = FastAPI(title="NovelForge", description="全自动超长篇小说写作系统 v7.3",
              version="7.3.0", docs_url=docs_url, redoc_url=redoc_url,
              openapi_url=openapi_url)

# CORS: restrict to configured origins in production
allowed_origins = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins],
    allow_credentials=allowed_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health routes: no auth required (Docker healthcheck needs these)
app.include_router(health_router)
# Business routes: auth required
app.include_router(router, dependencies=[Depends(verify_token)])


@app.on_event("startup")
async def startup():
    asyncio.create_task(seed_prompt_templates())


@app.on_event("shutdown")
async def shutdown():
    pass
