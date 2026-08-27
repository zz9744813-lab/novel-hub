"""MemoryCompiler - generates L2 ten-chapter and L3 volume summaries.
Per §5 L2/L3 v7.3.

Batch2 fixes:
- Filter L1 by chapter_no range (join Chapter), not earliest 10 rows
- Use call_agent (bindings + route events + context packages)
- source_hash uses SHA-256, not Python hash()
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.caller import call_agent
from app.models import (
    Chapter,
    MemoryL1ChapterLedger,
    MemoryL2StageSummary,
    MemoryL3VolumeSummary,
    OutlineNode,
    OutlineVersion,
)

logger = logging.getLogger("novelforge.memory_compiler")


def volume_stage_window(
    chapter_no: int,
    chapter_from: int,
    chapter_to: int,
    *,
    stage_size: int = 10,
) -> tuple[int, int]:
    """Return the volume-local L2 window containing ``chapter_no``."""
    if stage_size < 1 or not chapter_from <= chapter_no <= chapter_to:
        raise ValueError("chapter must be inside a valid volume range")
    start = chapter_from + ((chapter_no - chapter_from) // stage_size) * stage_size
    return start, min(start + stage_size - 1, chapter_to)


def _sha256_json(obj) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def generate_l2(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_start: int,
    chapter_end: int,
    outline_version: int = 1,
) -> MemoryL2StageSummary | None:
    """Generate L2 summary for chapters [start, end] (typically 10 chapters)."""
    rows = await db.execute(
        select(MemoryL1ChapterLedger, Chapter.chapter_no)
        .join(Chapter, Chapter.id == MemoryL1ChapterLedger.chapter_id)
        .where(
            MemoryL1ChapterLedger.book_id == book_id,
            Chapter.chapter_no >= chapter_start,
            Chapter.chapter_no <= chapter_end,
        )
        .order_by(Chapter.chapter_no)
    )
    l1_pairs = rows.all()
    l1_data = [
        {"chapter_no": ch_no, "ledger": ledger.ledger_json}
        for ledger, ch_no in l1_pairs
    ]
    source_hash = _sha256_json(l1_data)
    existing = (
        await db.execute(
            select(MemoryL2StageSummary).where(
                MemoryL2StageSummary.book_id == book_id,
                MemoryL2StageSummary.chapter_range_start == chapter_start,
                MemoryL2StageSummary.chapter_range_end == chapter_end,
                MemoryL2StageSummary.outline_version == outline_version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.source_hash == source_hash
            and existing.status == "generated"
            and isinstance(existing.summary_json, dict)
            and not existing.summary_json.get("error")
        ):
            return existing

    nodes = await db.execute(
        select(OutlineNode)
        .join(OutlineVersion, OutlineVersion.id == OutlineNode.outline_version_id)
        .where(
            OutlineNode.book_id == book_id,
            OutlineVersion.version == outline_version,
            OutlineNode.chapter_no >= chapter_start,
            OutlineNode.chapter_no <= chapter_end,
        )
        .order_by(OutlineNode.chapter_no)
    )
    outline_goals = [
        {"chapter_no": n.chapter_no, "goal": n.goal} for n in nodes.scalars().all()
    ]

    # End the read transaction before network IO.  The same session may safely
    # acquire a new connection for the append after the model returns.
    await db.commit()

    # Release caller's session before LLM (call_agent opens its own sessions)
    user_content = json.dumps(
        {
            "task": "l2_stage_summary",
            "l1_ledgers": l1_data,
            "outline_goals": outline_goals,
            "chapter_range": [chapter_start, chapter_end],
            "instructions": (
                "将指定章节范围内的 L1 事实账本压缩为 L2 阶段摘要，"
                "提取：阶段目标、冲突变化、人物弧线、未决问题；"
                "summary_type 必须为 l2_stage。只输出 JSON。"
            ),
        },
        ensure_ascii=False,
    )

    # IMPORTANT: do not use the caller's open session during LLM await.
    # call_agent signature: (db, book_id, agent_role, user_content, ...)
    # We pass db only for API compatibility; call_agent uses its own short sessions.
    run, publishable, meta = await call_agent(
        book_id=book_id,
        agent_role="memory_compiler",
        user_content=user_content,
        l1_refs=[{"chapter_start": chapter_start, "chapter_end": chapter_end, "count": len(l1_data)}],
        assembly_manifest={
            "entries": [{"type": "l1_range", "start": chapter_start, "end": chapter_end}],
            "excluded_entries": [],
            "budget": {"max_context": 128000, "reserved_output": 4096, "used": len(user_content) // 4},
        },
    )
    meta = meta or {}

    if isinstance(publishable, dict):
        summary_json = publishable
    elif isinstance(publishable, str) and publishable:
        from app.gateway.normalizer import normalize_json
        normalized = normalize_json(publishable)
        summary_json = (
            normalized
            if isinstance(normalized, dict)
            else {"error": "invalid_l2_summary_shape"}
        )
    else:
        summary_json = {"error": meta.get("error") or meta.get("block_reason") or "empty"}
    if summary_json.get("summary_type") != "l2_stage":
        summary_json = {"error": "invalid_l2_summary_type"}

    source_run_id = run.id if run else uuid.uuid4()
    succeeded = bool(summary_json) and not (
        meta.get("error")
        or meta.get("block_reason")
        or (isinstance(summary_json, dict) and summary_json.get("error"))
    )

    if existing is None:
        l2 = MemoryL2StageSummary(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_range_start=chapter_start,
            chapter_range_end=chapter_end,
            outline_version=outline_version,
            source_hash=source_hash,
            status="generated" if succeeded else "degraded",
            summary_json=summary_json or {},
            source_run_id=source_run_id,
        )
        db.add(l2)
    else:
        l2 = existing
        l2.source_hash = source_hash
        l2.status = "generated" if succeeded else "degraded"
        l2.summary_json = summary_json or {}
        l2.source_run_id = source_run_id
    await db.flush()
    logger.info("L2 summary generated for chapters %s-%s", chapter_start, chapter_end)
    return l2


async def generate_l3(
    db: AsyncSession,
    book_id: uuid.UUID,
    volume_no: int,
    outline_version: int = 1,
    chapter_start: int | None = None,
    chapter_end: int | None = None,
) -> MemoryL3VolumeSummary | None:
    """Generate L3 volume summary from L2s in this volume range."""
    q = select(MemoryL2StageSummary).where(
        MemoryL2StageSummary.book_id == book_id,
        MemoryL2StageSummary.outline_version == outline_version,
    )
    if chapter_start is not None:
        q = q.where(MemoryL2StageSummary.chapter_range_end >= chapter_start)
    if chapter_end is not None:
        q = q.where(MemoryL2StageSummary.chapter_range_start <= chapter_end)
    q = q.order_by(MemoryL2StageSummary.chapter_range_start)

    l2s = await db.execute(q)
    l2_rows = l2s.scalars().all()
    l2_data = [
        {
            "range": [s.chapter_range_start, s.chapter_range_end],
            "summary": s.summary_json,
        }
        for s in l2_rows
    ]
    source_hash = _sha256_json(l2_data)
    existing = (
        await db.execute(
            select(MemoryL3VolumeSummary).where(
                MemoryL3VolumeSummary.book_id == book_id,
                MemoryL3VolumeSummary.volume_no == volume_no,
                MemoryL3VolumeSummary.outline_version == outline_version,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.source_hash == source_hash
            and existing.status == "generated"
            and isinstance(existing.summary_json, dict)
            and not existing.summary_json.get("error")
        ):
            return existing

    await db.commit()

    user_content = json.dumps(
        {
            "task": "l3_volume_summary",
            "l2_summaries": l2_data,
            "volume_no": volume_no,
            "chapter_range": [chapter_start, chapter_end],
            "instructions": (
                "将本卷所有 L2 阶段摘要压缩为 L3 卷级摘要，"
                "提取：本卷主线、支线、角色弧、关键状态变化、下一卷约束；"
                "summary_type 必须为 l3_volume。只输出 JSON。"
            ),
        },
        ensure_ascii=False,
    )

    run, publishable, meta = await call_agent(
        book_id=book_id,
        agent_role="memory_compiler",
        user_content=user_content,
        l2_refs=[{"volume_no": volume_no, "l2_count": len(l2_data)}],
        assembly_manifest={
            "entries": [{"type": "l2_volume", "volume_no": volume_no}],
            "excluded_entries": [],
            "budget": {"max_context": 128000, "reserved_output": 4096, "used": len(user_content) // 4},
        },
    )
    meta = meta or {}

    if isinstance(publishable, dict):
        summary_json = publishable
    elif isinstance(publishable, str) and publishable:
        from app.gateway.normalizer import normalize_json
        normalized = normalize_json(publishable)
        summary_json = (
            normalized
            if isinstance(normalized, dict)
            else {"error": "invalid_l3_summary_shape"}
        )
    else:
        summary_json = {"error": meta.get("error") or "empty"}
    if summary_json.get("summary_type") != "l3_volume":
        summary_json = {"error": "invalid_l3_summary_type"}

    source_run_id = run.id if run else uuid.uuid4()
    succeeded = bool(summary_json) and not (
        meta.get("error")
        or meta.get("block_reason")
        or (isinstance(summary_json, dict) and summary_json.get("error"))
    )
    if existing is None:
        l3 = MemoryL3VolumeSummary(
            id=uuid.uuid4(),
            book_id=book_id,
            volume_no=volume_no,
            outline_version=outline_version,
            source_hash=source_hash,
            status="generated" if succeeded else "degraded",
            summary_json=summary_json or {},
            source_run_id=source_run_id,
        )
        db.add(l3)
    else:
        l3 = existing
        l3.source_hash = source_hash
        l3.status = "generated" if succeeded else "degraded"
        l3.summary_json = summary_json or {}
        l3.source_run_id = source_run_id
    await db.flush()
    logger.info("L3 summary generated for volume %s", volume_no)
    return l3
