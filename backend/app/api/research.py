"""v9.1 Research REST API backed by PostgreSQL + ARQ (spec §19).

All reads hit the real `research_*` tables; task execution is enqueued to the
ARQ worker (never FastAPI BackgroundTasks).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.tables import (
    ResearchDocument,
    ResearchExport,
    ResearchSource,
    ResearchTask,
)

logger = logging.getLogger("novelforge.research_api")

router = APIRouter(prefix="/api/research", tags=["research"])

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class ResearchSourceOut(BaseModel):
    id: str
    code: str
    name: str
    base_url: str
    chapter_list_selector: str | None
    title_selector: str | None
    content_selector: str
    pagination_selector: str | None
    encoding: str
    rate_limit: float
    enabled: bool
    verification_status: str
    last_verified_at: datetime | None
    config: dict = Field(default_factory=dict)


class ResearchTaskOut(BaseModel):
    id: str
    book_id: str | None
    source_id: str
    source_code: str | None
    source_name: str | None
    target_url: str
    status: str
    progress: int
    discovered_count: int
    completed_count: int
    current_url: str | None
    error_code: str | None
    error_detail: dict | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


class ResearchDocumentSummary(BaseModel):
    id: str
    task_id: str
    ordinal: int
    title: str
    source_url: str
    char_count: int
    content_hash: str
    metadata: dict = Field(default_factory=dict)


class ResearchDocumentOut(ResearchDocumentSummary):
    content: str


class CreateTaskRequest(BaseModel):
    source_id: str
    target_url: str
    book_id: str | None = None


class TaskListResponse(BaseModel):
    tasks: list[ResearchTaskOut]
    total: int


class DocumentListResponse(BaseModel):
    documents: list[ResearchDocumentSummary]
    total: int


def _source_out(row: ResearchSource) -> ResearchSourceOut:
    return ResearchSourceOut(
        id=str(row.id),
        code=row.code,
        name=row.name,
        base_url=row.base_url,
        chapter_list_selector=row.chapter_list_selector,
        title_selector=row.title_selector,
        content_selector=row.content_selector,
        pagination_selector=row.pagination_selector,
        encoding=row.encoding,
        rate_limit=row.rate_limit,
        enabled=row.enabled,
        verification_status=row.verification_status,
        last_verified_at=row.last_verified_at,
        config=row.config_json or {},
    )


def _task_out(
    task: ResearchTask,
    source: ResearchSource | None = None,
) -> ResearchTaskOut:
    return ResearchTaskOut(
        id=str(task.id),
        book_id=str(task.book_id) if task.book_id else None,
        source_id=str(task.source_id),
        source_code=source.code if source else None,
        source_name=source.name if source else None,
        target_url=task.target_url,
        status=task.status,
        progress=task.progress,
        discovered_count=task.discovered_count,
        completed_count=task.completed_count,
        current_url=task.current_url,
        error_code=task.error_code,
        error_detail=task.error_detail,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _doc_summary(row: ResearchDocument) -> ResearchDocumentSummary:
    return ResearchDocumentSummary(
        id=str(row.id),
        task_id=str(row.task_id),
        ordinal=row.ordinal,
        title=row.title,
        source_url=row.source_url,
        char_count=row.char_count,
        content_hash=row.content_hash,
        metadata=row.metadata_json or {},
    )


async def _load_source(db: AsyncSession, source_id: str) -> ResearchSource:
    try:
        sid = uuid.UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="source_id must be a UUID")
    row = (
        await db.execute(select(ResearchSource).where(ResearchSource.id == sid))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"source '{source_id}' not found")
    return row


async def _load_task(db: AsyncSession, task_id: str) -> ResearchTask:
    try:
        tid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="task_id must be a UUID")
    row = (
        await db.execute(select(ResearchTask).where(ResearchTask.id == tid))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="task not found")
    return row


async def _enqueue_research_task(task_id: uuid.UUID) -> None:
    import redis.asyncio.connection as _rc

    _rc.AbstractConnection.lib_name = None
    _rc.AbstractConnection.lib_version = None
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    parts = redis_url.replace("redis://", "").split(":")
    host = parts[0]
    port = int(parts[1].split("/")[0]) if len(parts) > 1 else 6379
    pool = await create_pool(RedisSettings(host=host, port=port))
    try:
        await pool.enqueue_job(
            "run_research_task_job",
            str(task_id),
            _job_id=f"research:{task_id}",
        )
    finally:
        await pool.close()


def _validate_target_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=422, detail="target_url must be an absolute http(s) URL")
    return url


@router.get("/sources", response_model=list[ResearchSourceOut])
async def list_sources(
    include_disabled: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[ResearchSourceOut]:
    stmt = select(ResearchSource).order_by(ResearchSource.name)
    if not include_disabled:
        stmt = stmt.where(ResearchSource.enabled.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    return [_source_out(r) for r in rows]


@router.post("/tasks", response_model=ResearchTaskOut, status_code=201)
async def create_task(
    req: CreateTaskRequest,
    db: AsyncSession = Depends(get_db),
) -> ResearchTaskOut:
    source = await _load_source(db, req.source_id)
    if not source.enabled:
        raise HTTPException(status_code=409, detail=f"source '{source.code}' is disabled")
    target_url = _validate_target_url(req.target_url)

    book_id: uuid.UUID | None = None
    if req.book_id:
        try:
            book_id = uuid.UUID(req.book_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="book_id must be a UUID")

    task = ResearchTask(
        book_id=book_id,
        source_id=source.id,
        target_url=target_url,
        status="queued",
        progress=0,
        discovered_count=0,
        completed_count=0,
    )
    db.add(task)
    await db.flush()

    try:
        await _enqueue_research_task(task.id)
    except Exception as e:
        logger.error("research enqueue failed task=%s: %s", task.id, e)
        task.status = "failed"
        task.error_code = "ENQUEUE_FAILED"
        task.error_detail = {"detail": str(e)[:2000]}
        await db.commit()
        raise HTTPException(status_code=503, detail=f"worker enqueue failed: {e}")

    await db.commit()
    await db.refresh(task)
    return _task_out(task, source)


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    book_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> TaskListResponse:
    stmt = select(ResearchTask, ResearchSource).join(
        ResearchSource, ResearchTask.source_id == ResearchSource.id, isouter=True
    )
    count_stmt = select(func.count()).select_from(ResearchTask)

    if book_id:
        try:
            bid = uuid.UUID(book_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="book_id must be a UUID")
        stmt = stmt.where(ResearchTask.book_id == bid)
        count_stmt = count_stmt.where(ResearchTask.book_id == bid)
    if status:
        stmt = stmt.where(ResearchTask.status == status)
        count_stmt = count_stmt.where(ResearchTask.status == status)

    total = (await db.execute(count_stmt)).scalar_one()
    stmt = (
        stmt.order_by(ResearchTask.created_at.desc()).limit(limit).offset(offset)
    )
    rows = (await db.execute(stmt)).all()

    return TaskListResponse(
        tasks=[_task_out(t, s) for t, s in rows],
        total=total,
    )


@router.get("/tasks/{task_id}", response_model=ResearchTaskOut)
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> ResearchTaskOut:
    task = await _load_task(db, task_id)
    source = (
        await db.execute(
            select(ResearchSource).where(ResearchSource.id == task.source_id)
        )
    ).scalar_one_or_none()
    return _task_out(task, source)


@router.post("/tasks/{task_id}/cancel", response_model=ResearchTaskOut)
async def cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> ResearchTaskOut:
    task = await _load_task(db, task_id)
    source = (
        await db.execute(
            select(ResearchSource).where(ResearchSource.id == task.source_id)
        )
    ).scalar_one_or_none()

    if task.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"task already terminal (status: {task.status})",
        )
    if task.status == "queued":
        # never started: finalize directly; worker skips terminal tasks
        task.status = "cancelled"
        task.finished_at = datetime.now(timezone.utc)
    else:
        task.status = "cancel_requested"
    await db.commit()
    await db.refresh(task)
    return _task_out(task, source)


@router.delete("/tasks/{task_id}", response_model=ResearchTaskOut)
async def delete_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
) -> ResearchTaskOut:
    """Cancel alias (kept for older callers)."""
    return await cancel_task(task_id, db)


@router.get("/tasks/{task_id}/documents", response_model=DocumentListResponse)
async def list_task_documents(
    task_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> DocumentListResponse:
    task = await _load_task(db, task_id)
    count_stmt = (
        select(func.count())
        .select_from(ResearchDocument)
        .where(ResearchDocument.task_id == task.id)
    )
    total = (await db.execute(count_stmt)).scalar_one()
    stmt = (
        select(ResearchDocument)
        .where(ResearchDocument.task_id == task.id)
        .order_by(ResearchDocument.ordinal.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return DocumentListResponse(
        documents=[_doc_summary(r) for r in rows],
        total=total,
    )


@router.get("/documents/{document_id}", response_model=ResearchDocumentOut)
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
) -> ResearchDocumentOut:
    try:
        did = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="document_id must be a UUID")
    row = (
        await db.execute(select(ResearchDocument).where(ResearchDocument.id == did))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    return ResearchDocumentOut(
        **_doc_summary(row).model_dump(),
        content=row.content,
    )


class ExportRequest(BaseModel):
    format: str = "txt"


class ExportOut(BaseModel):
    id: str
    task_id: str
    format: str
    file_path: str
    content_hash: str
    byte_size: int
    document_count: int
    download_url: str


class ImportReferenceRequest(BaseModel):
    book_id: str
    mode: str = "all"
    document_ids: list[str] = Field(default_factory=list)


class ImportReferenceResponse(BaseModel):
    sample_ids: list[str]
    created: int
    deduped: int


def _export_out(row: ResearchExport, document_count: int) -> ExportOut:
    return ExportOut(
        id=str(row.id),
        task_id=str(row.task_id),
        format=row.format,
        file_path=row.file_path,
        content_hash=row.content_hash,
        byte_size=row.byte_size,
        document_count=document_count,
        download_url=f"/api/research/exports/{row.id}/download",
    )


async def _load_export(db: AsyncSession, export_id: str) -> ResearchExport:
    try:
        eid = uuid.UUID(export_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="export_id must be a UUID")
    row = (
        await db.execute(select(ResearchExport).where(ResearchExport.id == eid))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="export not found")
    return row


@router.post("/tasks/{task_id}/exports", response_model=ExportOut, status_code=201)
async def create_export(
    task_id: str,
    req: ExportRequest,
    db: AsyncSession = Depends(get_db),
) -> ExportOut:
    """Materialize a real TXT file from task documents (spec §25)."""
    from app.research.exporter import export_task_txt, load_task_documents

    if req.format != "txt":
        raise HTTPException(status_code=422, detail="only format 'txt' is supported")

    task = await _load_task(db, task_id)
    if task.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"cannot export task in status '{task.status}'",
        )

    documents = await load_task_documents(db, task.id)
    if not documents:
        raise HTTPException(status_code=409, detail="task has no documents")

    try:
        export = await export_task_txt(db, task=task, documents=documents)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await db.commit()
    return _export_out(export, len(documents))


@router.get("/exports/{export_id}", response_model=ExportOut)
async def get_export(
    export_id: str,
    db: AsyncSession = Depends(get_db),
) -> ExportOut:
    from app.research.exporter import load_task_documents

    export = await _load_export(db, export_id)
    documents = await load_task_documents(db, export.task_id)
    return _export_out(export, len(documents))


@router.get("/exports/{export_id}/download")
async def download_export(
    export_id: str,
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Stream the real export file (never a placeholder path)."""
    from pathlib import Path

    export = await _load_export(db, export_id)
    path = Path(export.file_path)
    if not path.is_file() or path.stat().st_size == 0:
        raise HTTPException(status_code=404, detail="export file missing on disk")
    return FileResponse(
        path,
        media_type="text/plain",
        filename=f"research-task-{str(export.task_id)[:8]}.{export.format}",
    )


@router.post("/tasks/{task_id}/import-reference", response_model=ImportReferenceResponse)
async def import_reference(
    task_id: str,
    req: ImportReferenceRequest,
    db: AsyncSession = Depends(get_db),
) -> ImportReferenceResponse:
    """Write real ReferenceSample rows via the shared reference service (spec §26)."""
    from app.research.exporter import load_task_documents
    from app.services.reference_service import create_reference_sample_from_text

    task = await _load_task(db, task_id)
    if task.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"cannot import task in status '{task.status}'",
        )
    try:
        book_id = uuid.UUID(req.book_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="book_id must be a UUID")

    documents = await load_task_documents(db, task.id)
    if not documents:
        raise HTTPException(status_code=409, detail="task has no documents")

    if req.mode == "selected":
        if not req.document_ids:
            raise HTTPException(
                status_code=422, detail="document_ids required for mode='selected'"
            )
        wanted = set()
        for did in req.document_ids:
            try:
                wanted.add(uuid.UUID(did))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"invalid document id: {did}")
        documents = [d for d in documents if d.id in wanted]
        if not documents:
            raise HTTPException(status_code=404, detail="no matching documents")
    elif req.mode != "all":
        raise HTTPException(status_code=422, detail="mode must be 'all' or 'selected'")

    source_code = None
    if documents and documents[0].metadata_json:
        source_code = documents[0].metadata_json.get("source_code")

    sample_ids: list[str] = []
    created = 0
    deduped = 0
    for doc in documents:
        name_prefix = source_code or "research"
        filename = f"{name_prefix}_{str(task.id)[:8]}_{doc.ordinal:04d}.txt"
        sample, was_created = await create_reference_sample_from_text(
            db,
            book_id=book_id,
            text=doc.content,
            filename=filename,
            source_kind="research",
            source_ref={"task_id": str(task.id), "document_id": str(doc.id)},
        )
        sample_ids.append(str(sample.id))
        if was_created:
            created += 1
        else:
            deduped += 1

    await db.commit()
    return ImportReferenceResponse(
        sample_ids=sample_ids,
        created=created,
        deduped=deduped,
    )
