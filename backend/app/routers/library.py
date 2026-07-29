"""Library / bookshelf API (v8.0) — independent router."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import library_service

router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("/books")
async def get_library_books(db: AsyncSession = Depends(get_db)):
    items = await library_service.list_bookshelf(db)
    return {"books": items, "count": len(items), "total": len(items)}


@router.get("/books/{book_id}/home")
async def get_book_home(book_id: str, db: AsyncSession = Depends(get_db)):
    try:
        bid = uuid.UUID(book_id)
    except ValueError:
        raise HTTPException(400, "invalid book_id")
    data = await library_service.book_home_summary(db, bid)
    if not data:
        raise HTTPException(404, "book not found")
    return data


@router.get("/books/{book_id}/context-preview")
async def get_book_context_preview(
    book_id: str,
    chapter_no: int = 1,
    agent_role: str = "draft_writer",
    db: AsyncSession = Depends(get_db),
):
    """Dry-run assembler kinds for import-derived bible (no chapter run)."""
    try:
        bid = uuid.UUID(book_id)
    except ValueError:
        raise HTTPException(400, "invalid book_id")
    return await library_service.preview_context_kinds(
        db, bid, chapter_no=chapter_no, agent_role=agent_role
    )


@router.get("/features")
async def get_features():
    from app.config import settings

    return {
        "FEATURE_LIBRARY_V2": bool(getattr(settings, "feature_library_v2", True)),
        "FEATURE_IMPORT_V2": bool(getattr(settings, "feature_import_v2", True)),
        "FEATURE_PROMPT_STUDIO": bool(getattr(settings, "feature_prompt_studio", True)),
    }
