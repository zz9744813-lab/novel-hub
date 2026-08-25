"""v9.7 Quality / AI-Tone / Technique API surface (spec §32)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AIToneFinding, QualitySignal, TechniqueCard, TechniqueCardUsage
from app.quality.signals import digest, latest_signals

router = APIRouter(tags=["v9.7-quality"])


# ── quality (spec §27) ──


@router.get("/api/books/{book_id}/quality/overview")
async def quality_overview(book_id: str, db: AsyncSession = Depends(get_db)):
    sigs = await latest_signals(db, book_id=uuid.UUID(book_id), limit=500)
    return {"metrics": digest(sigs), "sample": len(sigs)}


@router.get("/api/books/{book_id}/quality/trends")
async def quality_trends(book_id: str, db: AsyncSession = Depends(get_db)):
    from app.editorial.metrics import book_quality_metrics

    return await book_quality_metrics(db, uuid.UUID(book_id))


@router.get("/api/books/{book_id}/quality/root-causes")
async def quality_root_causes(book_id: str, db: AsyncSession = Depends(get_db)):
    sigs = await latest_signals(db, book_id=uuid.UUID(book_id), limit=500)
    by_attr: dict = {}
    for s in sigs:
        if s.metric_name in ("root_cause_share", "plot_root_cause_share") and s.label:
            by_attr[s.label] = round(by_attr.get(s.label, 0) + (s.numeric_value or 0), 3)
    return {"root_causes": dict(sorted(by_attr.items(), key=lambda kv: -kv[1]))}


@router.get("/api/books/{book_id}/quality/agent-performance")
async def quality_agent_performance(book_id: str, db: AsyncSession = Depends(get_db)):
    sigs = await latest_signals(db, book_id=uuid.UUID(book_id), limit=500)
    by_role: dict = {}
    for s in sigs:
        if not s.agent_role:
            continue
        b = by_role.setdefault(s.agent_role, {})
        b[s.metric_name] = s.numeric_value
    return {"agents": by_role}


@router.get("/api/books/{book_id}/quality/model-performance")
async def quality_model_performance(book_id: str, db: AsyncSession = Depends(get_db)):
    from app.model_autopilot.performance import aggregate

    return await aggregate(db, "24h")


# ── ai-tone (spec §25–§26) ──


@router.get("/api/books/{book_id}/ai-tone/summary")
async def ai_tone_summary(book_id: str, db: AsyncSession = Depends(get_db)):
    rows = (
        (
            await db.execute(
                select(AIToneFinding).where(AIToneFinding.book_id == uuid.UUID(book_id))
                .order_by(AIToneFinding.created_at.desc()).limit(100)
            )
        )
        .scalars()
        .all()
    )
    confirmed = sum(1 for f in rows if f.human_disposition == "confirmed")
    dismissed = sum(1 for f in rows if f.human_disposition == "dismissed")
    return {
        "findings": len(rows),
        "confirmed": confirmed,
        "dismissed": dismissed,
        "confirmed_rate": round(confirmed / len(rows), 3) if rows else None,
    }


@router.get("/api/chapters/{chapter_id}/ai-tone/findings")
async def ai_tone_findings(chapter_id: str, db: AsyncSession = Depends(get_db)):
    rows = (
        (
            await db.execute(
                select(AIToneFinding).where(AIToneFinding.chapter_id == uuid.UUID(chapter_id))
                .order_by(AIToneFinding.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": str(f.id),
                "rule_id": f.rule_id,
                "severity": f.severity,
                "excerpt": f.excerpt,
                "confidence": f.confidence,
                "style_override": f.style_override,
                "human_disposition": f.human_disposition,
            }
            for f in rows
        ]
    }


async def _disposition(finding_id: str, disposition: str, corrected_category: str | None, db: AsyncSession):
    from app.style.ai_tone.lint import apply_human_disposition

    finding = (
        await db.execute(select(AIToneFinding).where(AIToneFinding.id == uuid.UUID(finding_id)))
    ).scalar_one_or_none()
    if finding is None:
        raise HTTPException(404, "finding not found")
    result = await apply_human_disposition(
        db, finding=finding, disposition=disposition, corrected_category=corrected_category
    )
    await db.commit()
    return result


@router.post("/api/ai-tone/findings/{finding_id}/confirm")
async def ai_tone_confirm(finding_id: str, db: AsyncSession = Depends(get_db)):
    return await _disposition(finding_id, "confirmed", None, db)


@router.post("/api/ai-tone/findings/{finding_id}/dismiss")
async def ai_tone_dismiss(finding_id: str, db: AsyncSession = Depends(get_db)):
    return await _disposition(finding_id, "dismissed", None, db)


@router.post("/api/ai-tone/findings/{finding_id}/correct")
async def ai_tone_correct(finding_id: str, corrected_category: str, db: AsyncSession = Depends(get_db)):
    return await _disposition(finding_id, "corrected", corrected_category, db)


# ── technique (spec §23) ──


@router.get("/api/books/{book_id}/techniques")
async def techniques(book_id: str, db: AsyncSession = Depends(get_db)):
    rows = (
        (
            await db.execute(
                select(TechniqueCard).where(TechniqueCard.book_id == uuid.UUID(book_id))
                .order_by(TechniqueCard.status, TechniqueCard.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": str(t.id),
                "technique_type": t.technique_type,
                "name": t.name,
                "mechanism": t.mechanism,
                "status": t.status,
                "support_count": t.support_count,
                "confidence": t.confidence,
            }
            for t in rows
        ]
    }


@router.get("/api/books/{book_id}/techniques/effectiveness")
async def technique_effectiveness(book_id: str, db: AsyncSession = Depends(get_db)):
    rows = (
        (
            await db.execute(
                select(TechniqueCardUsage).where(TechniqueCardUsage.book_id == uuid.UUID(book_id))
                .order_by(TechniqueCardUsage.created_at.desc()).limit(500)
            )
        )
        .scalars()
        .all()
    )
    return {
        "usages": len(rows),
        "effective": sum(1 for r in rows if r.effective is True),
        "ineffective": sum(1 for r in rows if r.effective is False),
        "pending": sum(1 for r in rows if r.effective is None),
    }


@router.post("/api/books/{book_id}/techniques/{technique_id}/activate")
async def technique_activate(book_id: str, technique_id: str, db: AsyncSession = Depends(get_db)):
    card = (
        await db.execute(
            select(TechniqueCard).where(
                TechniqueCard.id == uuid.UUID(technique_id),
                TechniqueCard.book_id == uuid.UUID(book_id),
            )
        )
    ).scalar_one_or_none()
    if card is None:
        raise HTTPException(404, "technique not found")
    card.status = "active"
    await db.commit()
    return {"status": card.status}


@router.post("/api/books/{book_id}/techniques/{technique_id}/deactivate")
async def technique_deactivate(book_id: str, technique_id: str, db: AsyncSession = Depends(get_db)):
    card = (
        await db.execute(
            select(TechniqueCard).where(
                TechniqueCard.id == uuid.UUID(technique_id),
                TechniqueCard.book_id == uuid.UUID(book_id),
            )
        )
    ).scalar_one_or_none()
    if card is None:
        raise HTTPException(404, "technique not found")
    card.status = "disabled"
    await db.commit()
    return {"status": card.status}
