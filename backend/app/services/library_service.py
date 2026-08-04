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

    # BookProfile map for logline/genre fallback
    profiles: dict[Any, dict[str, Any]] = {}
    try:
        from app.models.tables import BookProfile

        if books:
            ids = [b.id for b in books]
            for bp in (
                await db.execute(select(BookProfile).where(BookProfile.book_id.in_(ids)))
            ).scalars().all():
                profiles[bp.book_id] = {
                    "logline": bp.logline,
                    "genre": bp.genre,
                    "themes": bp.themes,
                    "tone": bp.tone,
                    "synopsis": bp.synopsis,
                }
    except Exception as e:
        logger.debug("book_profile map skip: %s", e)

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
        # merge BookProfile when book-level fields empty (import path)
        genre = getattr(b, "genre", None)
        logline = getattr(b, "logline", None)
        prof = profiles.get(b.id)
        if prof:
            if not logline:
                logline = prof.get("logline")
            if not genre:
                genre = prof.get("genre")
            if not tags and genre:
                tags = [genre]
            if not tags and prof.get("themes"):
                tags = list(prof.get("themes") or [])[:5]
        out.append(
            {
                "book_id": str(b.id),
                "title": b.title,
                "subtitle": getattr(b, "subtitle", None),
                "source_import_session_id": (
                    str(b.source_import_session_id) if getattr(b, "source_import_session_id", None) else None
                ),
                "cover_url": cover,
                "cover_generated": bool(cover),
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
                "genre": genre,
                "logline": logline,
            }
        )
    return out


async def book_home_summary(db: AsyncSession, book_id: UUID) -> dict[str, Any]:
    from app.models.tables import (
        BookProfile,
        CharacterCard,
        CharacterRelationship,
        LocationCard,
        OutlineVolume,
        PlotThread,
        WorldRule,
        WritingConstraint,
    )

    book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one_or_none()
    if not book:
        return {}
    items = await list_bookshelf(db)
    card = next((x for x in items if x["book_id"] == str(book_id)), None)
    if not card:
        return {}

    async def _count(model, extra=None):
        q = select(func.count()).select_from(model).where(model.book_id == book_id)
        if extra is not None:
            q = q.where(extra)
        return int((await db.execute(q)).scalar() or 0)

    char_n = await _count(CharacterCard)
    rule_n = await _count(WorldRule)
    outline_n = await _count(OutlineNode)
    loc_n = await _count(LocationCard)
    plot_n = await _count(PlotThread)
    wc_n = await _count(WritingConstraint)
    vol_n = await _count(OutlineVolume)
    rel_n = await _count(CharacterRelationship)

    chars = (
        await db.execute(select(CharacterCard).where(CharacterCard.book_id == book_id).limit(12))
    ).scalars().all()
    locs = (
        await db.execute(select(LocationCard).where(LocationCard.book_id == book_id).limit(12))
    ).scalars().all()
    plots = (
        await db.execute(select(PlotThread).where(PlotThread.book_id == book_id).limit(12))
    ).scalars().all()
    rules = (
        await db.execute(select(WorldRule).where(WorldRule.book_id == book_id).limit(12))
    ).scalars().all()
    wcs = (
        await db.execute(
            select(WritingConstraint)
            .where(WritingConstraint.book_id == book_id)
            .order_by(WritingConstraint.priority.desc())
            .limit(12)
        )
    ).scalars().all()
    vols = (
        await db.execute(
            select(OutlineVolume).where(OutlineVolume.book_id == book_id).order_by(OutlineVolume.volume_no).limit(8)
        )
    ).scalars().all()
    outline_rows = (
        await db.execute(
            select(OutlineNode)
            .where(OutlineNode.book_id == book_id)
            .order_by(OutlineNode.chapter_no)
            .limit(12)
        )
    ).scalars().all()
    bp = (
        await db.execute(select(BookProfile).where(BookProfile.book_id == book_id))
    ).scalar_one_or_none()
    profile = None
    if bp:
        profile = {
            "logline": bp.logline,
            "synopsis": bp.synopsis,
            "genre": bp.genre,
            "themes": bp.themes,
            "tone": bp.tone,
            "core_loop": bp.core_loop,
        }
        if not card.get("logline") and bp.logline:
            card = {**card, "logline": bp.logline}
        if not card.get("genre") and bp.genre:
            card = {**card, "genre": bp.genre}

    next_action = "继续写下一章"
    if card.get("active_task"):
        next_action = card["active_task"]["label"]
    elif card.get("unresolved_risk_count"):
        next_action = "处理待确认项"
    elif outline_n == 0:
        next_action = "完善大纲后开始写作"
    elif char_n == 0:
        next_action = "补充人物设定"

    return {
        "book": card,
        "profile": profile,
        "counts": {
            "characters": char_n,
            "world_rules": rule_n,
            "outline_nodes": outline_n,
            "locations": loc_n,
            "plot_threads": plot_n,
            "writing_constraints": wc_n,
            "volumes": vol_n,
            "relationships": rel_n,
        },
        "entities": {
            "characters": [
                {"id": str(c.id), "name": c.name, "role": c.role, "description": (c.description or "")[:200]}
                for c in chars
            ],
            "locations": [
                {"id": str(l.id), "name": l.name, "description": (l.description or "")[:200]}
                for l in locs
            ],
            "plot_threads": [
                {"id": str(t.id), "name": t.name, "status": t.status, "description": (t.description or "")[:200]}
                for t in plots
            ],
            "world_rules": [
                {"id": str(r.id), "rule_key": r.rule_key, "description": (r.description or "")[:200]}
                for r in rules
            ],
            "writing_constraints": [
                {
                    "id": str(w.id),
                    "title": w.title,
                    "constraint_type": w.constraint_type,
                    "is_hard": w.is_hard,
                    "body": (w.body or "")[:200],
                }
                for w in wcs
            ],
            "volumes": [
                {
                    "id": str(v.id),
                    "volume_no": v.volume_no,
                    "title": v.title,
                    "chapter_from": v.chapter_from,
                    "chapter_to": v.chapter_to,
                }
                for v in vols
            ],
            "outline_preview": [
                {"id": str(n.id), "chapter_no": n.chapter_no, "title": n.title, "goal": (n.goal or "")[:160]}
                for n in outline_rows
            ],
        },
        "next_action": next_action,
        "context_kinds_expected": [
            "book_profile",
            "character_cards",
            "location_cards",
            "world_rule",
            "writing_constraints",
            "plot_thread",
            "current_volume",
            "outline_node",
        ],
    }


async def preview_context_kinds(
    db: AsyncSession,
    book_id: UUID,
    *,
    chapter_no: int = 1,
    agent_role: str = "draft_writer",
) -> dict[str, Any]:
    """Dry-run ContextAssembler kinds for a book without starting a chapter run."""
    from app.engine.context_assembler import assemble_context, ASSEMBLER_VERSION

    node = (
        await db.execute(
            select(OutlineNode)
            .where(OutlineNode.book_id == book_id, OutlineNode.chapter_no == chapter_no)
            .limit(1)
        )
    ).scalar_one_or_none()
    if not node:
        node = (
            await db.execute(
                select(OutlineNode)
                .where(OutlineNode.book_id == book_id)
                .order_by(OutlineNode.chapter_no)
                .limit(1)
            )
        ).scalar_one_or_none()
    if not node:
        return {
            "ok": False,
            "error": "no_outline_node",
            "assembler_version": ASSEMBLER_VERSION,
            "kinds": {},
            "items": [],
        }
    ch = int(node.chapter_no or chapter_no or 1)
    pkg = await assemble_context(
        db,
        book_id,
        node,
        scene_plan={"goal": node.goal, "scenes": []},
        forced_dependencies=[],
        retrieved_evidence=[],
        previous_scene_tail="",
        current_chapter=ch,
        agent_role=agent_role,
    )
    kinds: dict[str, int] = {}
    for it in pkg.get("items") or []:
        k = it.get("kind") or "unknown"
        kinds[k] = kinds.get(k, 0) + 1
    sample = [
        {
            "kind": it.get("kind"),
            "priority": it.get("priority"),
            "required": it.get("required"),
            "reason": it.get("reason"),
            "source_id": it.get("source_id"),
            "estimated_tokens": it.get("estimated_tokens"),
        }
        for it in (pkg.get("items") or [])[:40]
    ]
    return {
        "ok": True,
        "assembler_version": pkg.get("assembler_version") or ASSEMBLER_VERSION,
        "agent_role": agent_role,
        "chapter_no": ch,
        "outline_node_id": str(node.id),
        "used_tokens": pkg.get("used_tokens"),
        "budget_mode": pkg.get("budget_mode"),
        "manifest_hash": pkg.get("manifest_hash"),
        "kinds": dict(sorted(kinds.items(), key=lambda x: (-x[1], x[0]))),
        "item_count": len(pkg.get("items") or []),
        "items_sample": sample,
    }
