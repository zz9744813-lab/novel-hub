"""v9.7 QualitySignalService: unified feedback bus (spec §14, §28).

Human review, review-agent, style verifier, AI-Tone, CCNE, drift and gateway
all write signals here; Experience Learning, Prompt Evolution and Model
Autopilot read from here. No more three separate truths.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QualitySignal

SIGNAL_SOURCES = (
    "human_review", "review_agent", "style_verifier", "ai_tone_lint",
    "ccne", "drift_audit", "state_extractor", "model_gateway", "prompt_experiment",
)


async def emit_signal(
    db: AsyncSession,
    *,
    book_id,
    metric_name: str,
    signal_type: str,
    numeric_value: float | None = None,
    label: str | None = None,
    severity: str | None = None,
    agent_role: str | None = None,
    chapter_id=None,
    chapter_run_id=None,
    agent_run_id=None,
    source: str | None = None,
    source_ref: str | None = None,
    confidence: float | None = None,
    dedupe_key: str | None = None,
) -> QualitySignal | None:
    """Idempotent signal write; returns None for unsupported signal_type."""
    if signal_type not in SIGNAL_SOURCES:
        return None
    row = QualitySignal(
        id=uuid.uuid4(),
        book_id=book_id,
        chapter_id=chapter_id,
        chapter_run_id=chapter_run_id,
        agent_run_id=agent_run_id,
        agent_role=agent_role,
        signal_type=signal_type,
        metric_name=metric_name,
        numeric_value=numeric_value,
        label=label,
        severity=severity,
        source=source,
        source_ref=source_ref,
        confidence=confidence,
    )
    db.add(row)
    return row


async def latest_signals(
    db: AsyncSession,
    *,
    book_id,
    agent_role: str | None = None,
    metric_name: str | None = None,
    limit: int = 200,
) -> list[QualitySignal]:
    stmt = select(QualitySignal).where(QualitySignal.book_id == book_id)
    if agent_role:
        stmt = stmt.where(QualitySignal.agent_role == agent_role)
    if metric_name:
        stmt = stmt.where(QualitySignal.metric_name == metric_name)
    rows = (await db.execute(stmt.order_by(QualitySignal.created_at.desc()).limit(limit))).scalars().all()
    return rows


def digest(signals: list[QualitySignal]) -> dict[str, Any]:
    """Metrics digest for dashboards — sums/averages by metric name."""
    out: dict[str, dict] = {}
    for s in signals:
        bucket = out.setdefault(s.metric_name, {"count": 0, "sum": 0.0, "avg": None, "last": None})
        bucket["count"] += 1
        if s.numeric_value is not None:
            bucket["sum"] += s.numeric_value
            bucket["avg"] = round(bucket["sum"] / bucket["count"], 4)
        bucket["last"] = s.label or s.numeric_value
    return out
