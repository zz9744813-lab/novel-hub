"""Research source health + automatic certification (spec §6, §18).

A source's `verification_status` is no longer a hand-edited JSON flag — it is
recomputed from probe evidence. This module owns:
- config_hash: deterministic hash of a source's selector rules
- recompute_source_status: verified/degraded/broken/blocked from recent probes
- build_health_report: /api/research/health aggregate
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ResearchSource, ResearchSourceProbeRun

VERIFIED_WINDOW_DAYS = 7
VERIFIED_MIN_PASSES = 3
VERIFIED_MIN_URLS = 2
VERIFIED_MIN_CHARS = 500


def config_hash(config: dict) -> str:
    """Deterministic hash of a source's selector rules (spec §5, §6)."""
    canonical = json.dumps(config or {}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def source_config_dict(source: ResearchSource) -> dict:
    """The selector subset that participates in rule identity."""
    return {
        "chapter_list_selector": source.chapter_list_selector,
        "title_selector": source.title_selector,
        "content_selector": source.content_selector,
        "pagination_selector": source.pagination_selector,
        "encoding": source.encoding,
        "rate_limit": source.rate_limit,
    }


async def recompute_source_status(db: AsyncSession, source: ResearchSource) -> str:
    """Recompute verification_status from recent probe runs (spec §6)."""
    since = datetime.now(timezone.utc) - timedelta(days=VERIFIED_WINDOW_DAYS)
    rows = (
        await db.execute(
            select(ResearchSourceProbeRun)
            .where(
                ResearchSourceProbeRun.source_id == source.id,
                ResearchSourceProbeRun.created_at >= since,
            )
            .order_by(ResearchSourceProbeRun.created_at.desc())
            .limit(50)
        )
    ).scalars().all()

    if not rows:
        return source.verification_status

    recent = rows[:20]
    passed = [r for r in recent if r.status == "passed"]
    blocked = [r for r in recent if r.status == "blocked"]
    failed = [r for r in recent if r.status == "failed"]

    # blocked: anti-bot / access-control dominant
    if blocked and len(blocked) >= max(1, int(len(recent) * 0.5)):
        return "blocked"

    # broken: HTTP reachable but selector never matched
    if failed and not passed:
        return "broken"

    # degraded: was verified but only failures recently
    if source.verification_status == "verified" and failed and not passed:
        return "degraded"

    # verified: enough independent, high-quality passes
    unique_urls = {r.test_url for r in passed}
    good_chars = any(r.extracted_chars >= VERIFIED_MIN_CHARS for r in passed)
    if (
        len(passed) >= VERIFIED_MIN_PASSES
        and len(unique_urls) >= VERIFIED_MIN_URLS
        and good_chars
    ):
        return "verified"

    return source.verification_status


async def build_health_report(db: AsyncSession) -> dict:
    """Aggregate health snapshot for /api/research/health (spec §18)."""
    sources = (await db.execute(select(ResearchSource))).scalars().all()
    enabled = [s for s in sources if s.enabled]
    counts: dict[str, int] = {}
    for s in enabled:
        counts[s.verification_status] = counts.get(s.verification_status, 0) + 1

    return {
        "enabled_sources": len(enabled),
        "verified_sources": counts.get("verified", 0),
        "degraded_sources": counts.get("degraded", 0),
        "blocked_sources": counts.get("blocked", 0),
        "broken_sources": counts.get("broken", 0),
        "experimental_sources": counts.get("experimental", 0),
        "disabled_sources": len(sources) - len(enabled),
    }
