"""Rule versioning (spec §5): rule changes create a new version and demote the source.

When a site redesigns, the old "verified" badge must not survive. Every rule
change bumps a version row (config_hash changes) and resets the source back to
`experimental` until re-probed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ResearchSourceVersion
from app.research.source_health import config_hash


async def bump_rule_version(
    db: AsyncSession,
    *,
    source_id: uuid.UUID,
    config: dict,
    created_by: str | None = None,
) -> ResearchSourceVersion:
    """Create a new rule version row; return it (caller still flushes)."""
    h = config_hash(config)
    maxv = (
        await db.execute(
            select(func.max(ResearchSourceVersion.version)).where(
                ResearchSourceVersion.source_id == source_id
            )
        )
    ).scalar() or 0
    row = ResearchSourceVersion(
        source_id=source_id,
        version=maxv + 1,
        config_hash=h,
        config_json=config,
        status="experimental",
        created_by=created_by,
        activated_at=datetime.now(timezone.utc),
    )
    db.add(row)
    return row
