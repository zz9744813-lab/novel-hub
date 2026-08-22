"""Idempotent research source seeding (spec §18).

On startup: backend/app/config/research_sources.json → research_sources rows.
The JSON ships inside the repo/image — never a runtime-only data/ path.
Existing rows are updated in place (selectors, rate limit, verification
status overrides are preserved for manually verified sources).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ResearchSource
from app.research.models import ResearchSourceConfig

logger = logging.getLogger("novelforge.research.seeding")

SOURCES_JSON = Path(__file__).resolve().parent.parent / "config" / "research_sources.json"

# Values seeded from JSON never override manual verification work:
# once a source is promoted to verified/disabled by ops, reseeding keeps it.
_PROTECTED_STATUSES = {"verified", "disabled"}


def load_source_entries() -> list[dict]:
    if not SOURCES_JSON.exists():
        logger.warning("research sources config missing: %s", SOURCES_JSON)
        return []
    try:
        data = json.loads(SOURCES_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error("research sources config unreadable: %s", e)
        return []
    entries = data.get("sources") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and (e.get("code") or e.get("name"))]


async def seed_research_sources(db: AsyncSession) -> dict:
    """Upsert sources from JSON. Returns {'inserted': n, 'updated': n}."""
    entries = load_source_entries()
    if not entries:
        return {"inserted": 0, "updated": 0}

    existing = {
        row.code: row
        for row in (await db.execute(select(ResearchSource))).scalars()
    }

    inserted = 0
    updated = 0
    for entry in entries:
        cfg = ResearchSourceConfig.from_json_entry(entry)
        if not cfg.base_url or not cfg.content_selector:
            logger.warning("skip incomplete source entry: %s", cfg.code)
            continue
        row = existing.get(cfg.code)
        if row is None:
            db.add(
                ResearchSource(
                    code=cfg.code,
                    name=cfg.name,
                    base_url=cfg.base_url,
                    chapter_list_selector=cfg.chapter_list_selector,
                    title_selector=cfg.title_selector,
                    content_selector=cfg.content_selector,
                    pagination_selector=cfg.pagination_selector,
                    encoding=cfg.encoding,
                    rate_limit=cfg.rate_limit,
                    enabled=True,
                    verification_status=cfg.verification_status,
                    config_json=cfg.extra,
                )
            )
            inserted += 1
            continue

        # refresh rule fields, keep protected verification states
        row.name = cfg.name
        row.base_url = cfg.base_url
        row.chapter_list_selector = cfg.chapter_list_selector
        row.title_selector = cfg.title_selector
        row.content_selector = cfg.content_selector
        row.pagination_selector = cfg.pagination_selector
        row.encoding = cfg.encoding
        row.rate_limit = cfg.rate_limit
        if row.verification_status not in _PROTECTED_STATUSES:
            row.verification_status = cfg.verification_status
        row.config_json = cfg.extra
        updated += 1

    await db.flush()
    logger.info("research sources seeded: inserted=%s updated=%s", inserted, updated)
    return {"inserted": inserted, "updated": updated}
