"""MemoryCompiler - generates L2 ten-chapter and L3 volume summaries.
Per §5 L2/L3 v7.3.
FIX P0-8: stream_agent_call import fixed + chapter range query
"""
import uuid
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.gateway.model_gateway import stream_agent_call  # FIX: was stream_agent_call (didn't exist)
from app.config import settings
from app.models import (
    MemoryL1ChapterLedger, MemoryL2StageSummary, MemoryL3VolumeSummary,
    OutlineNode, Chapter, ChapterVersion,
)

logger = logging.getLogger("novelforge.memory_compiler")


async def generate_l2(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_start: int,
    chapter_end: int,
    outline_version: int = 1,
) -> MemoryL2StageSummary | None:
    """Generate L2 summary for chapters [start, end] (typically 10 chapters).
    FIX P0-8: Query L1s by chapter range, not just .limit(10)
    """
    # Get chapter IDs in range
    ch_result = await db.execute(
        select(Chapter).where(
            Chapter.book_id == book_id,
            Chapter.chapter_no >= chapter_start,
            Chapter.chapter_no <= chapter_end,
        )
    )
    chapters = ch_result.scalars().all()
    chapter_ids = [c.id for c in chapters]

    if not chapter_ids:
        logger.warning(f"No chapters found for L2 [{chapter_start}-{chapter_end}]")
        return None

    # FIX: query L1s by chapter_id in range
    l1s = await db.execute(
        select(MemoryL1ChapterLedger).where(
            MemoryL1ChapterLedger.book_id == book_id,
            MemoryL1ChapterLedger.chapter_id.in_(chapter_ids),
        ).order_by(MemoryL1ChapterLedger.created_at)
    )
    l1_data = [l.ledger_json for l in l1s.scalars().all()]

    # Get outline nodes in range
    nodes = await db.execute(
        select(OutlineNode).where(
            OutlineNode.book_id == book_id,
            OutlineNode.chapter_no >= chapter_start,
            OutlineNode.chapter_no <= chapter_end,
        ).order_by(OutlineNode.chapter_no)
    )
    outline_goals = [{"chapter_no": n.chapter_no, "goal": n.goal} for n in nodes.scalars().all()]

    user_content = json.dumps({
        "l1_ledgers": l1_data,
        "outline_goals": outline_goals,
        "chapter_range": [chapter_start, chapter_end],
    }, ensure_ascii=False)

    result = await stream_agent_call(
        system_prompt="你是记忆编译器。请将10章的L1事实账本压缩为L2阶段摘要，提取：阶段目标、冲突变化、人物弧线、未决问题。输出JSON。",
        user_content=user_content,
        model=settings.query_model,
        temperature=0.1,
    )

    from app.gateway.normalizer import normalize_json
    summary_json = normalize_json(result.final_content) if result.final_content else {"raw": result.final_content or ""}

    source_hash = str(hash(json.dumps(l1_data, ensure_ascii=False, sort_keys=True)))
    source_run_id = uuid.uuid4()

    l2 = MemoryL2StageSummary(
        id=uuid.uuid4(),
        book_id=book_id,
        chapter_range_start=chapter_start,
        chapter_range_end=chapter_end,
        outline_version=outline_version,
        source_hash=source_hash,
        status="generated",
        summary_json=summary_json or {},
        source_run_id=source_run_id,
    )
    db.add(l2)
    await db.flush()
    logger.info(f"L2 summary generated for chapters {chapter_start}-{chapter_end}")
    return l2


async def generate_l3(
    db: AsyncSession,
    book_id: uuid.UUID,
    volume_no: int,
    outline_version: int = 1,
) -> MemoryL3VolumeSummary | None:
    """Generate L3 volume summary from L2s in this volume.
    FIX P0-8: Query L2s by volume, not all L2s.
    """
    # Get the chapter range for this volume from outline nodes
    # FIX: query L2s by volume via outline nodes
    nodes = await db.execute(
        select(OutlineNode).where(
            OutlineNode.book_id == book_id,
            OutlineNode.volume_no == volume_no,
        ).order_by(OutlineNode.chapter_no)
    )
    volume_nodes = nodes.scalars().all()

    if not volume_nodes:
        logger.warning(f"No outline nodes found for volume {volume_no}")
        return None

    chap_start = min(n.chapter_no for n in volume_nodes)
    chap_end = max(n.chapter_no for n in volume_nodes)

    # Query L2s that overlap with this volume's chapter range
    l2s = await db.execute(
        select(MemoryL2StageSummary).where(
            MemoryL2StageSummary.book_id == book_id,
            MemoryL2StageSummary.chapter_range_start >= chap_start,
            MemoryL2StageSummary.chapter_range_end <= chap_end,
        )
    )
    l2_data = [s.summary_json for s in l2s.scalars().all()]

    user_content = json.dumps({
        "l2_summaries": l2_data,
        "volume_no": volume_no,
        "chapter_range": [chap_start, chap_end],
    }, ensure_ascii=False)

    result = await stream_agent_call(
        system_prompt="你是卷级记忆编译器。请将本卷所有L2阶段摘要压缩为L3卷级摘要，提取：本卷主线、支线、角色弧、关键状态变化、下一卷约束。输出JSON。",
        user_content=user_content,
        model=settings.query_model,
        temperature=0.1,
    )

    from app.gateway.normalizer import normalize_json
    summary_json = normalize_json(result.final_content) if result.final_content else {}

    l3 = MemoryL3VolumeSummary(
        id=uuid.uuid4(),
        book_id=book_id,
        volume_no=volume_no,
        outline_version=outline_version,
        source_hash=str(hash(json.dumps(l2_data, ensure_ascii=False, sort_keys=True))),
        status="generated",
        summary_json=summary_json or {},
        source_run_id=uuid.uuid4(),
    )
    db.add(l3)
    await db.flush()
    logger.info(f"L3 summary generated for volume {volume_no}")
    return l3
