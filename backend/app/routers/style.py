"""Style Intelligence Engine API (spec §21, §52).

Endpoints:
- POST /api/books/{book_id}/style-profile/analyze  -> build profile from references
- GET  /api/books/{book_id}/style-profile           -> latest profile
- POST /api/books/{book_id}/chapters/{no}/style-score -> score a chapter vs profile
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Chapter, ChapterVersion, StyleProfile
from app.style.service import (
    create_style_profile,
    load_reference_text,
    score_chapter_against_profile,
    upsert_chapter_score,
)

router = APIRouter(prefix="/api/books", tags=["style"])


def _uuid(v: str, field: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be a UUID")


class AnalyzeRequest(BaseModel):
    genre_hint: str | None = None
    text: str | None = None


class ScoreRequest(BaseModel):
    content: str | None = None


def _profile_out(p: StyleProfile) -> dict:
    return {
        "id": str(p.id),
        "book_id": str(p.book_id),
        "version": p.version,
        "status": p.status,
        "metric_vector": p.metric_vector,
        "metric_ranges": p.metric_ranges,
        "fingerprint": p.fingerprint,
        "narrative_profile": p.narrative_profile,
        "dialogue_profile": p.dialogue_profile,
        "rhythm_profile": p.rhythm_profile,
        "emotion_expression_profile": p.emotion_expression_profile,
        "technique_profile": p.technique_profile,
        "scene_mode_profiles": p.scene_mode_profiles,
        "confidence_by_dimension": p.confidence_by_dimension,
        "analyzer_version": p.analyzer_version,
        "metric_engine_version": p.metric_engine_version,
        "approved_by": p.approved_by,
        "approved_at": p.approved_at.isoformat() if p.approved_at else None,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


async def _latest_profile(db: AsyncSession, book_id: uuid.UUID) -> StyleProfile | None:
    return (
        await db.execute(
            select(StyleProfile)
            .where(StyleProfile.book_id == book_id)
            .order_by(StyleProfile.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _chapter_content(db: AsyncSession, book_id: uuid.UUID, chapter_no: int) -> str:
    ch = (
        await db.execute(
            select(Chapter).where(
                Chapter.book_id == book_id, Chapter.chapter_no == chapter_no
            )
        )
    ).scalar_one_or_none()
    if ch is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    ver = (
        await db.execute(
            select(ChapterVersion)
            .where(ChapterVersion.chapter_id == ch.id)
            .order_by(ChapterVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if ver is None:
        raise HTTPException(status_code=409, detail="chapter has no content")
    return ver.content


@router.post("/{book_id}/style-profile/analyze")
async def analyze_style_profile(
    book_id: str,
    req: AnalyzeRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    bid = _uuid(book_id, "book_id")
    text = (req.text or "").strip() or await load_reference_text(db, bid)
    if not text.strip():
        raise HTTPException(
            status_code=409,
            detail="没有参考文本。请先在参考资料库导入参考作品。",
        )
    profile = await create_style_profile(
        db, book_id=bid, reference_text=text, genre_hint=req.genre_hint
    )
    await db.commit()
    await db.refresh(profile)
    return _profile_out(profile)


@router.get("/{book_id}/style-profile")
async def get_style_profile(
    book_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    bid = _uuid(book_id, "book_id")
    profile = await _latest_profile(db, bid)
    if profile is None:
        return {"status": "not_found", "profile": None}
    return {"status": "ok", "profile": _profile_out(profile)}


@router.post("/{book_id}/chapters/{chapter_no}/style-score")
async def score_chapter_style(
    book_id: str,
    chapter_no: int,
    req: ScoreRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    bid = _uuid(book_id, "book_id")
    profile = await _latest_profile(db, bid)
    if profile is None:
        raise HTTPException(
            status_code=409,
            detail="尚未生成文风档案。请先执行风格分析。",
        )
    content = (req.content or "").strip() or await _chapter_content(db, bid, chapter_no)
    if not content.strip():
        raise HTTPException(status_code=409, detail="章节没有正文内容")
    row = await upsert_chapter_score(
        db, book_id=bid, chapter_no=chapter_no, content=content, profile=profile
    )
    await db.commit()
    await db.refresh(row)
    return {
        "chapter_no": row.chapter_no,
        "surface_score": row.surface_score,
        "rhythm_score": row.rhythm_score,
        "dialogue_score": row.dialogue_score,
        "narrative_score": row.narrative_score,
        "emotion_score": row.emotion_score,
        "voice_score": row.voice_score,
        "overall_score": row.overall_score,
        "distance_to_profile": row.distance_to_profile,
    }
