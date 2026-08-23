"""System metadata API — version / build info (spec v9.2 §18, §19).

Purpose: the UI must never hardcode a version label (the old "v8.0" string was
burned into Sidebar/App). All build/version metadata comes from this endpoint.
"""
from __future__ import annotations

import os

from fastapi import APIRouter

router = APIRouter(prefix="/api/system", tags=["system"])

APP_VERSION = "9.2.0"
PIPELINE_VERSION = "v9.2"


def _git_sha() -> str:
    return os.environ.get("GIT_SHA", "").strip()


def _alembic_revision() -> str:
    return os.environ.get("ALEMBIC_REVISION", "").strip() or "0013"


@router.get("/version")
async def get_version() -> dict:
    """Return build/version metadata consumed by the frontend header/sidebar."""
    return {
        "app_version": APP_VERSION,
        "git_sha": _git_sha(),
        "pipeline_version": PIPELINE_VERSION,
        "alembic_revision": _alembic_revision(),
    }
