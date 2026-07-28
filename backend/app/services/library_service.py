"""Stable CSS cover placeholder + library aggregation (v8.0)."""
from __future__ import annotations

import hashlib
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Book, Chapter, ChapterRun, OutlineNode
from app.state_machine import ChapterState

logger = logging.getLogger("novelforge.library")

PALETTES = [
    ("#1a1a2e", "#e94560", "#0f3460"),
    ("#0d1b2a", "#1b263b", "#415a77"),
    ("#2b2d42", "#8d99ae", "#ef233c"),
    ("#1b4332", "#40916c", "#d8f3dc"),
    ("#3d0c11", "#8b1e3f", "#f4a261"),
    ("#10002b", "#5a189a", "#e0aaff"),
    ("#001219", "#005f73", "#94d2bd"),
    ("#22223b", "#4a4e69", "#9a8c98"),
]


def stable_cover_style(book_id: str | UUID) -> dict[str, str]:
    h = hashlib.sha256(str(book_id).encode()).hexdigest()
    idx = int(h[:8], 16) % len(PALETTES)
    bg, accent, mid = PALETTES[idx]
    angle = int(h[8:10], 16) % 360
    return {
        "background": f"linear-gradient({angle}deg, {bg} 0%, {mid} 55%, {accent} 100%)",
        "accent": accent,
        "bg": bg,
    }


def _lifecycle_from_book(book: Book, active_run: bool, needs_human: bool) -> str:
    base = getattr(book, "lifecycle_status", None) or "draft"
    if needs_human:
        return "needs_human"
    if active_run:
        return "writing"
    planned = book.planned_chapters or book.target_chapters or 0
    if planned and (book.finalized_chapters or 0) >= planned:
        return "completed"
    if (book.finalized_chapters or 0) > 0:
        return "writing" if base in ("draft", None, "") else base
    return base or "draft"


async def list_bookshelf(db: AsyncSession) -> list[dict[str, Any]]:
    books = (
        await db.execute(
            select(Book).order_by(Book.updated_at.desc().nullslast(), Book.created_at.desc())
        )
    ).scalars().all()
    if not books:
        return []

    book_ids = [b.id for b in books]

    outline_counts = {
        r[0]: r[1]
        for r in (
            await db.execute(
                select(OutlineNode.book_id, func.count())
                .where(OutlineNode.book_id.in_(book_ids))
                .group_by(OutlineNode.book_id)
            )
        ).all()
    }

    # Active production runs (status values used by current pipeline)
    active_runs = (
        await db.execute(
            select(ChapterRun)
            .where(
                ChapterRun.book_id.in_(book_ids),
                ChapterRun.status.in_(
                    ["queued", "running", "leased", "started", "dispatching", "in_progress"]
                ),
            )
            .order_by(ChapterRun.created_at.desc())
        )
    ).scalars().all()
    run_by_book: dict[UUID, ChapterRun] = {}
    for r in active_runs:
        run_by_book.setdefault(r.book_id, r)

    nh_counts = {
        r[0]: r[1]
        for r in (
            await db.execute(
                select(Chapter.book_id, func.count())
                .where(
                    Chapter.book_id.in_(book_ids),
                    Chapter.status == ChapterState.NEEDS_HUMAN.value,
                )
                .group_by(Chapter.book_id)
            )
        ).all()
    }

    # Optional risk count — use SAVEPOINT so a failure cannot poison the request txn
    hi_counts: dict[UUID, int] = {}
    try:
        async with db.begin_nested():
            rows = (
                await db.execute(
                    text(
                        """
                        SELECT book_id, COUNT(*)::int
                        FROM human_interventions
                        WHERE book_id = ANY(:ids)
                          AND COALESCE(status, 'open') NOT IN ('resolved', 'closed', 'done')
                        GROUP BY book_id
                        """
                    ),
                    {"ids": book_ids},
                )
            ).all()
            hi_counts = {r[0]: r[1] for r in rows}
    except Exception as e:
        logger.warning("human_interventions risk count skipped: %s", e)
        hi_counts = {}

    max_final = {
        r[0]: r[1]
        for r in (
            await db.execute(
                select(Chapter.book_id, func.max(Chapter.chapter_no))
                .where(
                    Chapter.book_id.in_(book_ids),
                    Chapter.status == ChapterState.FINALIZED.value,
                )
                .group_by(Chapter.book_id)
            )
        ).all()
    }

    out: list[dict[str, Any]] = []
    for b in books:
        ar = run_by_book.get(b.id)
        nh = int(nh_counts.get(b.id, 0))
        risk = nh + int(hi_counts.get(b.id, 0))
        planned = b.planned_chapters or b.target_chapters or outline_counts.get(b.id) or None
        life = _lifecycle_from_book(b, bool(ar), nh > 0)
        active_task = None
        if ar:
            active_task = {
                "type": "chapter_run",
                "label": f"正在生成第 {ar.chapter_no} 章",
                "progress": None,
                "run_id": str(ar.id),
                "chapter_no": ar.chapter_no,
            }
        elif nh > 0:
            active_task = {
                "type": "needs_human",
                "label": f"{nh} 章等待人工处理",
                "progress": None,
            }
        tags = b.tags if isinstance(getattr(b, "tags", None), list) else []
        if not tags and getattr(b, "genre", None):
            tags = [b.genre]
        cover = None
        if getattr(b, "cover_thumb_path", None):
            cover = f"/api/library/books/{b.id}/cover?thumb=1"
        elif getattr(b, "cover_path", None):
            cover = f"/api/library/books/{b.id}/cover"
        out.append(
            {
                "book_id": str(b.id),
                "title": b.title,
                "subtitle": getattr(b, "subtitle", None),
                "cover_url": cover,
                "cover_style": stable_cover_style(b.id),
                "tags": tags[:5],
                "lifecycle_status": life,
                "finalized_chapters": b.finalized_chapters or 0,
                "planned_chapters": planned,
                "finalized_words": b.finalized_words or 0,
                "current_chapter_no": max_final.get(b.id) or getattr(b, "current_chapter_no", None),
                "active_task": active_task,
                "unresolved_risk_count": risk,
                "updated_at": (b.updated_at.isoformat() if b.updated_at else None),
                "genre": getattr(b, "genre", None),
                "logline": getattr(b, "logline", None),
            }
        )
    return out


async def book_home_summary(db: AsyncSession, book_id: UUID) -> dict[str, Any]:
    from app.models.tables import CharacterCard, WorldRule

    book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one_or_none()
    if not book:
        return {}
    items = await list_bookshelf(db)
    card = next((x for x in items if x["book_id"] == str(book_id)), None)
    if not card:
        return {}
    char_n = (
        await db.execute(
            select(func.count()).select_from(CharacterCard).where(CharacterCard.book_id == book_id)
        )
    ).scalar() or 0
    rule_n = (
        await db.execute(
            select(func.count()).select_from(WorldRule).where(WorldRule.book_id == book_id)
        )
    ).scalar() or 0
    outline_n = (
        await db.execute(
            select(func.count()).select_from(OutlineNode).where(OutlineNode.book_id == book_id)
        )
    ).scalar() or 0
    next_action = "继续写下一章"
    if card.get("active_task"):
        next_action = card["active_task"]["label"]
    elif card.get("unresolved_risk_count"):
        next_action = "处理待确认项"
    return {
        "book": card,
        "counts": {
            "characters": int(char_n),
            "world_rules": int(rule_n),
            "outline_nodes": int(outline_n),
        },
        "next_action": next_action,
    }
