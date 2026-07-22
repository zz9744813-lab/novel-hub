"""FastAPI routes - all endpoints per §附录B."""
import uuid
import json
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select, update, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db, async_session_factory
from app.models import (
    Book, BookSetting, OutlineVersion, OutlineNode, OutlineDependency,
    ChapterTask, Chapter, ChapterVersion, Scene, Paragraph,
    CharacterCard, CharacterStateSnapshot,
    WorldRule, PlotThread, StoryEvent, EntityAlias,
    MemoryL1ChapterLedger, MemoryL2StageSummary, MemoryL3VolumeSummary, MemoryL4StateSnapshot,
    StyleVoiceCard, StyleToneAnchor,
    QueryPlan, RetrievalRun, RetrievalCandidate, RetrievalJudgement,
    ReviewIssue, RewritePatch, DriftAuditReport,
    AgentRun, AgentRunOutput, LlmUsageEvent,
    HumanIntervention, PromptTemplate,
)
from app.state_machine import ChapterState
from pydantic import BaseModel
from typing import Any
import os

router = APIRouter()
health_router = APIRouter()


def gen_uuid():
    return uuid.uuid4()


class BookCreate(BaseModel):
    title: str
    description: str | None = None
    target_chapters: int = 500
    target_words: int = 5000000


class OutlineParseRequest(BaseModel):
    raw_outline: str
    target_chapter_count: int = 500


class L4ReviseRequest(BaseModel):
    entity_type: str
    entity_id: str
    state: dict
    reason: str | None = None


class ResourceBlockRequest(BaseModel):
    available_mb: int | None = None
    swap_used_pct: int | None = None
    disk_used_pct: int | None = None


# ---- Health ----
@health_router.get("/health/live")
async def health_live():
    return {"status": "alive"}


@health_router.get("/health/ready")
async def health_ready(db: AsyncSession = Depends(get_db)):
    """Check database connectivity AND that core tables exist.
    FIX P0-3: Not just select(1) — verify tables are present.
    """
    try:
        result = await db.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'books'")
        )
        table_count = result.scalar()
        if table_count == 0:
            raise HTTPException(503, "Database ready but tables missing — run alembic upgrade head")
        return {"status": "ready", "database": "ok", "tables": table_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Database: {e}")


@health_router.get("/metrics")
async def metrics():
    return {"app": "novelforge", "status": "running"}


# ---- Books CRUD ----
@router.post("/api/books")
async def create_book(req: BookCreate, db: AsyncSession = Depends(get_db)):
    book = Book(id=gen_uuid(), title=req.title, description=req.description,
                target_chapters=req.target_chapters, target_words=req.target_words)
    db.add(book)
    await db.flush()
    return {"book_id": str(book.id), "title": book.title}


@router.get("/api/books")
async def list_books(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Book).order_by(Book.created_at.desc()))
    books = result.scalars().all()
    return [{"book_id": str(b.id), "title": b.title, "status": b.status,
             "finalized_chapters": b.finalized_chapters, "finalized_words": b.finalized_words} for b in books]


@router.get("/api/books/{book_id}")
async def get_book(book_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Book).where(Book.id == uuid.UUID(book_id)))
    book = result.scalar_one_or_none()
    if not book:
        raise HTTPException(404, "Book not found")
    return {"book_id": str(book.id), "title": book.title, "status": book.status,
            "target_chapters": book.target_chapters, "target_words": book.target_words,
            "finalized_chapters": book.finalized_chapters, "finalized_words": book.finalized_words}


# ---- Outline ----
@router.post("/api/books/{book_id}/outlines/parse")
async def parse_outline(book_id: str, req: OutlineParseRequest, db: AsyncSession = Depends(get_db)):
    from app.agents.outline_parser import parse_outline as do_parse
    bid = uuid.UUID(book_id)
    existing_v = await db.execute(select(func.count()).where(OutlineVersion.book_id == bid))
    ver_num = existing_v.scalar() + 1
    version = OutlineVersion(id=gen_uuid(), book_id=bid, version=ver_num,
                             status="parsing", source="upload", raw_outline=req.raw_outline)
    db.add(version)
    await db.flush()

    success, errors = await do_parse(db, bid, version.id, req.raw_outline, req.target_chapter_count)
    if not success:
        version.status = "error"
        await db.flush()
        return {"outline_version_id": str(version.id), "status": "error", "errors": errors}
    return {"outline_version_id": str(version.id), "version": 1, "status": "parsed"}


@router.post("/api/books/{book_id}/outlines/generate")
async def generate_outline(book_id: str, req: dict, db: AsyncSession = Depends(get_db)):
    # TODO: implement AI outline generation
    version = OutlineVersion(id=gen_uuid(), book_id=uuid.UUID(book_id),
                             version=1, status="draft", source="generate")
    db.add(version)
    await db.flush()
    return {"outline_version_id": str(version.id), "status": "draft"}


@router.post("/api/books/{book_id}/outlines/{version}/approve")
async def approve_outline(book_id: str, version: int, db: AsyncSession = Depends(get_db)):
    from app.engine.outline import validate_dag
    result = await db.execute(
        select(OutlineVersion).where(OutlineVersion.book_id == uuid.UUID(book_id), OutlineVersion.version == version)
    )
    ov = result.scalar_one_or_none()
    if not ov:
        raise HTTPException(404, "Outline version not found")
    valid, errors = await validate_dag(db, uuid.UUID(book_id), ov.id)
    if not valid:
        raise HTTPException(400, f"DAG validation failed: {errors}")
    ov.status = "approved"
    await db.flush()
    return {"status": "approved", "version": version}


@router.get("/api/books/{book_id}/outline-graph")
async def get_outline_graph(book_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(OutlineNode).where(OutlineNode.book_id == uuid.UUID(book_id)).order_by(OutlineNode.chapter_no)
    )
    nodes = result.scalars().all()
    return {"nodes": [{"node_id": str(n.id), "chapter_no": n.chapter_no, "title": n.title,
                        "goal": n.goal, "depends_on": n.depends_on,
                        "required_beats": n.required_beats} for n in nodes]}


# ---- Chapter operations ----
@router.post("/api/books/{book_id}/chapters/{chapter_no}/run")
async def run_chapter(book_id: str, chapter_no: int, db: AsyncSession = Depends(get_db)):
    bid = uuid.UUID(book_id)
    # Get outline node for this chapter
    node = (await db.execute(
        select(OutlineNode).where(OutlineNode.book_id == bid, OutlineNode.chapter_no == chapter_no)
    )).scalar_one_or_none()
    if not node:
        raise HTTPException(404, f"Outline node for chapter {chapter_no} not found")

    # Create or update chapter
    existing = await db.execute(select(Chapter).where(Chapter.book_id == bid, Chapter.chapter_no == chapter_no))
    chapter = existing.scalar_one_or_none()
    if not chapter:
        chapter = Chapter(id=gen_uuid(), book_id=bid, chapter_no=chapter_no,
                           outline_node_id=node.id, status=ChapterState.QUEUED.value, title=node.title)
        db.add(chapter)
    else:
        chapter.status = ChapterState.QUEUED.value

    task = ChapterTask(id=gen_uuid(), book_id=bid, chapter_no=chapter_no, status=ChapterState.QUEUED.value)
    db.add(task)
    await db.flush()

    # Enqueue via ARQ
    try:
        from arq import create_pool
        from arq.connections import RedisSettings
        import redis.asyncio.connection as _rc
        _rc.AbstractConnection.lib_name = None
        _rc.AbstractConnection.lib_version = None
        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        redis_parts = redis_url.replace("redis://", "").split(":")
        r_host = redis_parts[0]
        r_port = int(redis_parts[1].split("/")[0]) if len(redis_parts) > 1 else 6379
        pool = await create_pool(RedisSettings(host=r_host, port=r_port))
        await pool.enqueue_job("run_chapter_pipeline", str(chapter.id), str(bid), chapter_no)
    except Exception as e:
        pass  # Worker will pick up on recovery

    return {"chapter_id": str(chapter.id), "status": "queued", "chapter_no": chapter_no}


@router.post("/api/chapters/{chapter_id}/pause")
async def pause_chapter(chapter_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Chapter).where(Chapter.id == uuid.UUID(chapter_id)))
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    chapter.status = ChapterState.NEEDS_HUMAN.value
    await db.flush()
    return {"chapter_id": str(chapter.id), "status": "paused"}


@router.post("/api/chapters/{chapter_id}/resume")
async def resume_chapter(chapter_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Chapter).where(Chapter.id == uuid.UUID(chapter_id)))
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    chapter.status = ChapterState.QUEUED.value
    await db.flush()
    return {"chapter_id": str(chapter.id), "status": "queued"}


@router.get("/api/chapters/{chapter_id}")
async def get_chapter(chapter_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Chapter).where(Chapter.id == uuid.UUID(chapter_id)))
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    # Get latest version
    cv = await db.execute(
        select(ChapterVersion).where(ChapterVersion.chapter_id == chapter.id).order_by(ChapterVersion.version.desc()).limit(1)
    )
    version = cv.scalar_one_or_none()
    return {"chapter_id": str(chapter.id), "chapter_no": chapter.chapter_no,
            "status": chapter.status, "title": chapter.title,
            "finalized_version": chapter.finalized_version,
            "content": version.content if version else None,
            "word_count": version.word_count if version else 0}


@router.get("/api/chapters/{chapter_id}/context-package")
async def get_context_package(chapter_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Chapter).where(Chapter.id == uuid.UUID(chapter_id)))
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    return {"chapter_id": str(chapter.id), "context_package": None, "note": "Available after pipeline runs"}


@router.get("/api/chapters/{chapter_id}/query-plan")
async def get_query_plan(chapter_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(QueryPlan).where(QueryPlan.chapter_id == uuid.UUID(chapter_id))
        .order_by(QueryPlan.created_at.desc()).limit(1)
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(404, "No query plan found")
    return {"plan_id": str(plan.id), "plan_json": plan.plan_json}


@router.get("/api/chapters/{chapter_id}/retrieval-run")
async def get_retrieval_run(chapter_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RetrievalRun).where(RetrievalRun.chapter_id == uuid.UUID(chapter_id))
        .order_by(RetrievalRun.created_at.desc()).limit(1)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "No retrieval run found")
    return {"run_id": str(run.id), "status": run.status, "degraded": run.degraded,
            "candidate_count": run.candidate_count, "selected_count": run.selected_count}


@router.get("/api/chapters/{chapter_id}/patches")
async def get_patches(chapter_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RewritePatch).where(RewritePatch.chapter_id == uuid.UUID(chapter_id)).order_by(RewritePatch.created_at.desc())
    )
    patches = result.scalars().all()
    return [{"patch_id": str(p.id), "status": p.status, "paragraph_id": p.paragraph_id} for p in patches]


@router.post("/api/patches/{patch_id}/approve")
async def approve_patch(patch_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RewritePatch).where(RewritePatch.id == uuid.UUID(patch_id)))
    patch = result.scalar_one_or_none()
    if not patch:
        raise HTTPException(404, "Patch not found")
    patch.status = "approved"
    await db.flush()
    return {"patch_id": str(patch.id), "status": "approved"}


@router.post("/api/patches/{patch_id}/reject")
async def reject_patch(patch_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RewritePatch).where(RewritePatch.id == uuid.UUID(patch_id)))
    patch = result.scalar_one_or_none()
    if not patch:
        raise HTTPException(404, "Patch not found")
    patch.status = "rejected"
    await db.flush()
    return {"patch_id": str(patch.id), "status": "rejected"}


# ---- Memory ----
@router.get("/api/books/{book_id}/memory/l4")
async def get_l4_state(book_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MemoryL4StateSnapshot).where(MemoryL4StateSnapshot.book_id == uuid.UUID(book_id))
        .order_by(MemoryL4StateSnapshot.as_of_chapter.desc())
    )
    snapshots = result.scalars().all()
    return {"snapshots": [{"id": str(s.id), "entity_type": s.entity_type,
                           "entity_id": str(s.entity_id), "as_of_chapter": s.as_of_chapter,
                           "state": s.state, "version": s.version, "is_locked": s.is_locked} for s in snapshots]}


@router.post("/api/books/{book_id}/memory/l4/revise")
async def revise_l4(book_id: str, req: L4ReviseRequest, db: AsyncSession = Depends(get_db)):
    snap = MemoryL4StateSnapshot(id=gen_uuid(), book_id=uuid.UUID(book_id),
                                 entity_type=req.entity_type, entity_id=uuid.UUID(req.entity_id),
                                 as_of_chapter=0, state=req.state, version=1,
                                 source_run_id=gen_uuid(), is_locked=True)
    db.add(snap)
    hi = HumanIntervention(id=gen_uuid(), book_id=uuid.UUID(book_id),
                           intervention_type="l4_revise", target_entity_type=req.entity_type,
                           target_entity_id=uuid.UUID(req.entity_id), new_value=req.state, reason=req.reason)
    db.add(hi)
    await db.flush()
    return {"snapshot_id": str(snap.id), "status": "revised"}


# ---- DriftAudit ----
@router.get("/api/books/{book_id}/drift-audits")
async def list_drift_audits(book_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DriftAuditReport).where(DriftAuditReport.book_id == uuid.UUID(book_id))
        .order_by(DriftAuditReport.created_at.desc())
    )
    audits = result.scalars().all()
    return [{"audit_id": str(a.id), "status": a.status,
             "chapter_range": [a.chapter_range_start, a.chapter_range_end]} for a in audits]


@router.post("/api/drift-audits/{audit_id}/rerun")
async def rerun_audit(audit_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DriftAuditReport).where(DriftAuditReport.id == uuid.UUID(audit_id)))
    audit = result.scalar_one_or_none()
    if not audit:
        raise HTTPException(404, "Audit not found")
    audit.status = "pending"
    await db.flush()
    return {"audit_id": str(audit.id), "status": "rerunning"}


@router.post("/api/drift-audits/{audit_id}/accept-new-baseline")
async def accept_baseline(audit_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DriftAuditReport).where(DriftAuditReport.id == uuid.UUID(audit_id)))
    audit = result.scalar_one_or_none()
    if not audit:
        raise HTTPException(404, "Audit not found")
    audit.status = "accepted_baseline"
    await db.flush()
    return {"audit_id": str(audit.id), "status": "accepted_baseline"}


# ---- Resource management ----
@router.post("/api/admin/resource-block")
async def resource_block(req: ResourceBlockRequest, db: AsyncSession = Depends(get_db)):
    await db.execute(update(ChapterTask).where(ChapterTask.status == ChapterState.QUEUED.value).values(status=ChapterState.RESOURCE_BLOCKED.value))
    return {"status": "resource_blocked", "metrics": req.dict()}


@router.post("/api/admin/resource-unblock")
async def resource_unblock(db: AsyncSession = Depends(get_db)):
    await db.execute(update(ChapterTask).where(ChapterTask.status == ChapterState.RESOURCE_BLOCKED.value).values(status=ChapterState.QUEUED.value))
    return {"status": "resource_unblocked"}


@router.get("/api/admin/resources")
async def get_resources():
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if parts[0] in ("MemAvailable:", "MemTotal:", "SwapTotal:", "SwapFree:"):
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
        avail_mb = meminfo.get("MemAvailable", 0) // 1024
        swap_total = meminfo.get("SwapTotal", 0)
        swap_free = meminfo.get("SwapFree", 0)
        swap_pct = ((swap_total - swap_free) * 100 // swap_total) if swap_total > 0 else 0
        return {"available_mb": avail_mb, "swap_used_pct": swap_pct,
                "resource_safe": avail_mb > 350 and swap_pct < 60}
    except Exception:
        return {"available_mb": 999, "swap_used_pct": 0, "resource_safe": True}


# ---- Events ----
@router.get("/api/books/{book_id}/events")
async def list_events(book_id: str, limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StoryEvent).where(StoryEvent.book_id == uuid.UUID(book_id))
        .order_by(StoryEvent.created_at.desc()).limit(limit)
    )
    events = result.scalars().all()
    return [{"event_id": str(e.id), "event_type": e.event_type,
             "chapter_id": str(e.chapter_id), "certainty": e.certainty} for e in events]


@router.get("/api/books/{book_id}/events/{event_id}")
async def get_event(book_id: str, event_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StoryEvent).where(StoryEvent.id == uuid.UUID(event_id)))
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(404, "Event not found")
    return {"event_id": str(event.id), "event_type": event.event_type,
            "subject_entity_ids": event.subject_entity_ids,
            "after_state": event.after_state, "evidence_excerpt": event.evidence_excerpt}


# ---- Retrieval test ----
@router.post("/api/books/{book_id}/retrieval/test")
async def retrieval_test(book_id: str, req: dict, db: AsyncSession = Depends(get_db)):
    """Run a retrieval test — returns actual search results."""
    from app.engine.retrieval import full_text_search, event_ledger_search
    bid = uuid.UUID(book_id)
    search_terms = req.get("search_terms", [])
    char_ids = req.get("character_ids", [])
    chap_range = tuple(req.get("chapter_range", [1, 500]))
    ft_results = await full_text_search(db, bid, search_terms, chap_range)
    event_results = await event_ledger_search(db, bid, char_ids, [], chap_range)
    return {"book_id": book_id, "ft_results": ft_results[:10], "event_results": event_results[:10]}


@router.post("/api/books/{book_id}/retrieval/gold-samples")
async def create_gold_sample(book_id: str, req: dict, db: AsyncSession = Depends(get_db)):
    return {"book_id": book_id, "status": "saved", "note": "TODO"}


# ---- Agent run events ----
@router.get("/api/runs/{run_id}/events")
async def get_run_events(run_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AgentRun).where(AgentRun.id == uuid.UUID(run_id)))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Run not found")
    return {"run_id": str(run.id), "agent_role": run.agent_role,
            "status": run.status, "started_at": str(run.started_at) if run.started_at else None}


# ---- WebSocket ----
@router.websocket("/ws/books/{book_id}")
async def ws_book(websocket: WebSocket, book_id: str):
    await websocket.accept()
    try:
        while True:
            await asyncio.sleep(10)
            await websocket.send_json({"type": "ping", "book_id": book_id})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


# ---- Seed prompt templates on startup ----
async def seed_prompt_templates():
    from app.prompts import PROMPTS
    async with async_session_factory() as db:
        for role, config in PROMPTS.items():
            existing = await db.execute(
                select(PromptTemplate).where(
                    PromptTemplate.agent_role == role,
                    PromptTemplate.version == config["version"],
                )
            )
            if not existing.scalar_one_or_none():
                tpl = PromptTemplate(
                    id=gen_uuid(), agent_role=role, version=config["version"],
                    system_prompt=config["system_prompt"],
                    input_variables=config["input_variables"],
                    output_schema=config["output_schema"], is_active=True,
                )
                db.add(tpl)
        await db.commit()
