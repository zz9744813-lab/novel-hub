"""Global task center API.

This router normalizes task reads while delegating mutations to the owning
chapter/import state machines. Task IDs are type-scoped (``type:uuid``) so
identical UUID values from different tables can never address the wrong task.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Book, ChapterRun, ImportSession, ResearchSession
from app.services.task_service import (
    build_task_id,
    filter_and_paginate_tasks,
    normalize_task_type,
    parse_task_id,
    serialize_chapter_run,
    serialize_import_session,
    serialize_research_session,
    task_actions,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _uuid(value: str, field: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(400, f"invalid {field}") from exc


async def _chapter_tasks(db: AsyncSession, status: str | None, book_id: uuid.UUID | None):
    query = select(ChapterRun, Book.title).join(Book, Book.id == ChapterRun.book_id)
    if status:
        query = query.where(ChapterRun.status == status)
    if book_id:
        query = query.where(ChapterRun.book_id == book_id)
    rows = (await db.execute(query)).all()
    return [serialize_chapter_run(run, book_title=title) for run, title in rows]


async def _import_tasks(db: AsyncSession, status: str | None, book_id: uuid.UUID | None):
    query = select(ImportSession, Book.title).outerjoin(Book, Book.id == ImportSession.book_id)
    if status:
        query = query.where(ImportSession.status == status)
    if book_id:
        query = query.where(ImportSession.book_id == book_id)
    rows = (await db.execute(query)).all()
    return [serialize_import_session(session, book_title=title) for session, title in rows]


async def _research_tasks(db: AsyncSession, status: str | None, book_id: uuid.UUID | None):
    query = select(ResearchSession, Book.title).join(Book, Book.id == ResearchSession.book_id)
    if status:
        query = query.where(ResearchSession.status == status)
    if book_id:
        query = query.where(ResearchSession.book_id == book_id)
    rows = (await db.execute(query)).all()
    return [serialize_research_session(session, book_title=title) for session, title in rows]


@router.get("")
async def list_tasks(
    task_type: str | None = None,
    status: str | None = None,
    book_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Return one globally sortable task feed with stable pagination."""
    try:
        normalized_type = normalize_task_type(task_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    bid = _uuid(book_id, "book_id") if book_id else None

    rows: list[dict] = []
    if normalized_type in (None, "chapter"):
        rows.extend(await _chapter_tasks(db, status, bid))
    if normalized_type in (None, "import"):
        rows.extend(await _import_tasks(db, status, bid))
    if normalized_type in (None, "research"):
        rows.extend(await _research_tasks(db, status, bid))

    result = filter_and_paginate_tasks(
        rows,
        task_type=normalized_type,
        status=status,
        book_id=str(bid) if bid else None,
        page=page,
        page_size=page_size,
    )
    result["task_types"] = [normalized_type] if normalized_type else ["chapter", "import", "research"]
    return result


async def _find_task(db: AsyncSession, task_type: str, entity_id: uuid.UUID) -> dict | None:
    if task_type == "chapter":
        row = (
            await db.execute(
                select(ChapterRun, Book.title)
                .join(Book, Book.id == ChapterRun.book_id)
                .where(ChapterRun.id == entity_id)
            )
        ).first()
        return serialize_chapter_run(row[0], book_title=row[1]) if row else None
    if task_type == "import":
        row = (
            await db.execute(
                select(ImportSession, Book.title)
                .outerjoin(Book, Book.id == ImportSession.book_id)
                .where(ImportSession.id == entity_id)
            )
        ).first()
        return serialize_import_session(row[0], book_title=row[1]) if row else None
    row = (
        await db.execute(
            select(ResearchSession, Book.title)
            .join(Book, Book.id == ResearchSession.book_id)
            .where(ResearchSession.id == entity_id)
        )
    ).first()
    return serialize_research_session(row[0], book_title=row[1]) if row else None


@router.get("/{task_id}")
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)):
    try:
        task_type, entity_id = parse_task_id(task_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    item = await _find_task(db, task_type, entity_id)
    if item is None:
        raise HTTPException(404, "task not found")
    return item


async def _cancel_chapter(db: AsyncSession, run: ChapterRun) -> dict:
    run.control_requested = "cancel"
    run.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"status": "cancel_requested", "task_id": build_task_id("chapter", run.id)}


@router.post("/{task_id}/{action}")
async def operate_task(task_id: str, action: str, db: AsyncSession = Depends(get_db)):
    """Execute one operation using the owning task's durable state machine."""
    try:
        task_type, entity_id = parse_task_id(task_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    item = await _find_task(db, task_type, entity_id)
    if item is None:
        raise HTTPException(404, "task not found")
    if action not in item["actions"]:
        raise HTTPException(409, detail={"message": "action is not available", "actions": item["actions"]})

    if task_type == "chapter":
        run = (await db.execute(select(ChapterRun).where(ChapterRun.id == entity_id))).scalar_one()
        if action == "cancel":
            return await _cancel_chapter(db, run)
        if action == "pause":
            run.control_requested = "pause"
            run.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return {"status": "pause_requested", "task_id": task_id}
        if action in {"resume", "retry"}:
            from app.api.routes import resume_chapter

            return await resume_chapter(str(run.chapter_id), db)

    if task_type == "import":
        from app.routers.imports import cancel_session, requeue_analysis

        if action == "cancel":
            return await cancel_session(str(entity_id), db)
        if action == "retry":
            return await requeue_analysis(str(entity_id), db)

    if task_type == "research" and action == "cancel":
        session = (
            await db.execute(select(ResearchSession).where(ResearchSession.id == entity_id))
        ).scalar_one()
        session.status = "cancelled"
        await db.commit()
        return {"status": "cancelled", "task_id": task_id}

    raise HTTPException(409, "operation is not implemented for this task")
