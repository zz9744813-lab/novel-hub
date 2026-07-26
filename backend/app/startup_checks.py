"""Startup readiness checks (P0-07 / P0-09)."""
from __future__ import annotations

import os
import logging
from typing import Tuple

logger = logging.getLogger("novelforge.startup")

REQUIRED_ROLES = {
    "outline_parser",
    "chapter_planner",
    "draft_writer",
    "review_agent",
    "local_rewrite_editor",
    "state_extractor",
    "drift_audit",
    "query_planner",
    "evidence_ranker",
    "aileak_judge",
    "reference_analyzer",
    "research_planner",
    "research_synthesizer",
    "memory_compiler",
}

PLACEHOLDER_KEYS = {
    "",
    "replace-with-real-key",
    "sk-test",
    "your-key-here",
    "xxx",
}


async def check_provider_ready() -> Tuple[bool, str]:
    import httpx

    base = (os.environ.get("PRIMARY_BASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("PRIMARY_API_KEY") or "").strip()
    if not base:
        return False, "PRIMARY_BASE_URL empty"
    if key in PLACEHOLDER_KEYS or key.startswith("replace-"):
        return False, "PRIMARY_API_KEY is placeholder"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{base}/models",
                headers={"Authorization": f"Bearer {key}"},
            )
        if r.status_code >= 400:
            return False, f"/models HTTP {r.status_code}"
        return True, "ok"
    except Exception as e:
        return False, f"provider unreachable: {e}"


async def check_required_bindings() -> Tuple[bool, str]:
    from sqlalchemy import select
    from app.database import async_session_factory
    from app.models.tables import AgentModelBinding

    async with async_session_factory() as db:
        result = await db.execute(
            select(AgentModelBinding).where(AgentModelBinding.scope_type == "global")
        )
        have = {b.agent_role for b in result.scalars().all()}
    missing = sorted(REQUIRED_ROLES - have)
    if missing:
        return False, f"missing bindings: {missing}"
    return True, "ok"


async def check_db_ready() -> Tuple[bool, str]:
    from sqlalchemy import text
    from app.database import async_session_factory

    try:
        async with async_session_factory() as db:
            await db.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as e:
        return False, str(e)


async def run_all_checks() -> Tuple[bool, dict]:
    report = {}
    ok_db, msg = await check_db_ready()
    report["db"] = msg
    ok_p, msg = await check_provider_ready()
    report["provider"] = msg
    ok_b, msg = await check_required_bindings()
    report["bindings"] = msg
    return ok_db and ok_p and ok_b, report
