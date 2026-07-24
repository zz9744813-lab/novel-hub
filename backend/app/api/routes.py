"""FastAPI routes - all endpoints per §附录B."""
import uuid
import json
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from sqlalchemy import select, update, func
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
@router.get("/health/live")
@router.get("/api/health/live")
async def health_live():
    return {"status": "alive"}


@router.get("/health/ready")
@router.get("/api/health/ready")
async def health_ready(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(1))
        return {"status": "ready", "database": "ok"}
    except Exception as e:
        raise HTTPException(503, f"Database: {e}")


@router.get("/metrics")
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
@router.post("/api/books/{book_id}/outlines/upload")
async def upload_outline_file(
    book_id: str,
    file: UploadFile = File(...),
    target_chapter_count: int = Form(500),
    db: AsyncSession = Depends(get_db),
):
    """Upload outline document and parse via AI.

    Supports: .txt .md .docx .pdf .rtf .csv .json .html .xml (max 5MB).
    """
    from app.engine.file_extract import extract_text, ALLOWED_EXTENSIONS

    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "File too large. Max 5MB.")
    if len(content) == 0:
        raise HTTPException(400, "Empty file.")

    try:
        raw_text = extract_text(content, filename=file.filename, content_type=file.content_type)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # Create outline version and parse
    from app.agents.outline_parser import parse_outline as do_parse
    bid = uuid.UUID(book_id)
    existing_v = await db.execute(select(func.count()).where(OutlineVersion.book_id == bid))
    ver_num = existing_v.scalar() + 1
    version = OutlineVersion(
        id=gen_uuid(), book_id=bid, version=ver_num,
        status="parsing", source="upload", raw_outline=raw_text,
    )
    db.add(version)
    await db.flush()

    success, errors = await do_parse(db, bid, version.id, raw_text, target_chapter_count)
    if not success:
        version.status = "error"
        await db.flush()
        return {"outline_version_id": str(version.id), "status": "error", "errors": errors}
    return {
        "outline_version_id": str(version.id),
        "version": ver_num,
        "status": "parsed",
        "filename": file.filename,
        "chars": len(raw_text),
    }


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
    return {"outline_version_id": str(version.id), "version": ver_num, "status": "parsed"}


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
    bid = uuid.UUID(book_id)

    # Get the latest outline version (prefer approved, fallback to latest)
    version_result = await db.execute(
        select(OutlineVersion)
        .where(OutlineVersion.book_id == bid)
        .order_by(
            # approved versions first, then by version number desc
            (OutlineVersion.status == "approved").desc(),
            OutlineVersion.version.desc(),
        )
        .limit(1)
    )
    latest_version = version_result.scalar_one_or_none()

    if not latest_version:
        return {"nodes": [], "outline_version_id": None, "version": None, "status": None}

    # Query nodes for this version only
    result = await db.execute(
        select(OutlineNode)
        .where(
            OutlineNode.book_id == bid,
            OutlineNode.outline_version_id == latest_version.id,
        )
        .order_by(OutlineNode.chapter_no)
    )
    nodes = result.scalars().all()
    return {
        "nodes": [{"node_id": str(n.id), "chapter_no": n.chapter_no, "title": n.title,
                    "goal": n.goal, "depends_on": n.depends_on,
                    "required_beats": n.required_beats} for n in nodes],
        "outline_version_id": str(latest_version.id),
        "version": latest_version.version,
        "status": latest_version.status,
    }


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
    # Commit before enqueuing ARQ job - worker uses a separate DB connection
    # and cannot see uncommitted data
    await db.commit()

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
    return {"book_id": book_id, "results": [], "note": "TODO"}


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


# ═══════════════════════════════════════════════════════════════════════════════
# v7.4 API Routes: Model Bindings + Context Inspector + GenreProfile + Research
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/api/models/available")
async def list_available_models():
    """Pull model list from primary LLM gateway (New-API / OpenAI-compatible /v1/models)."""
    import httpx
    from app.config import settings

    base = (settings.primary_base_url or "").rstrip("/")
    if not base:
        raise HTTPException(503, "PRIMARY_BASE_URL not configured")

    url = f"{base}/models"
    headers = {}
    if settings.primary_api_key:
        headers["Authorization"] = f"Bearer {settings.primary_api_key}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch models from gateway: {e}") from e

    data = payload.get("data") if isinstance(payload, dict) else payload
    models: list[dict] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                models.append({
                    "id": item["id"],
                    "owned_by": item.get("owned_by"),
                    "object": item.get("object", "model"),
                })
            elif isinstance(item, str):
                models.append({"id": item, "owned_by": None, "object": "model"})

    # Deduplicate + sort
    seen = set()
    unique = []
    for m in models:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)
    unique.sort(key=lambda x: x["id"].lower())
    return {"models": unique, "count": len(unique), "source": base}


@router.get("/api/model-bindings")
async def list_model_bindings(db: AsyncSession = Depends(get_db)):
    from app.models.tables import AgentModelBinding
    result = await db.execute(select(AgentModelBinding).order_by(AgentModelBinding.agent_role))
    rows = result.scalars().all()
    return [{
        "id": str(r.id), "scope_type": r.scope_type, "scope_id": str(r.scope_id) if r.scope_id else None,
        "agent_role": r.agent_role, "provider": r.provider, "primary_model": r.primary_model,
        "fallback_model": r.fallback_model, "reasoning_mode": r.reasoning_mode,
        "version": r.version, "updated_by": r.updated_by, "updated_at": r.updated_at.isoformat(),
    } for r in rows]


@router.patch("/api/model-bindings/{binding_id}")
async def update_model_binding(binding_id: str, payload: dict, db: AsyncSession = Depends(get_db)):
    from app.v74_utils import ModelBindingService
    svc = ModelBindingService(db)
    binding = await svc.update_binding(
        binding_id=uuid.UUID(binding_id),
        new_provider=payload.get("provider"),
        new_model=payload.get("primary_model"),
        new_reasoning_mode=payload.get("reasoning_mode"),
        new_fallback=payload.get("fallback_model"),
        reason=payload.get("reason", "Manual update via API"),
        changed_by=payload.get("changed_by", "user"),
    )
    await db.commit()
    return {"id": str(binding.id), "status": "updated"}


@router.get("/api/model-change-log")
async def list_model_change_log(db: AsyncSession = Depends(get_db)):
    from app.models.tables import ModelChangeLog
    result = await db.execute(select(ModelChangeLog).order_by(ModelChangeLog.changed_at.desc()).limit(100))
    rows = result.scalars().all()
    return [{"id": str(r.id), "agent_role": r.agent_role, "old_model": r.old_model, "new_model": r.new_model,
             "reason": r.reason, "changed_by": r.changed_by, "changed_at": r.changed_at.isoformat()} for r in rows]


@router.get("/api/runs/{run_id}/model-route-events")
async def get_model_route_events(run_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import ModelRouteEvent
    result = await db.execute(
        select(ModelRouteEvent).where(ModelRouteEvent.run_id == uuid.UUID(run_id)).order_by(ModelRouteEvent.attempt_no)
    )
    rows = result.scalars().all()
    return [{"attempt_no": r.attempt_no, "configured_model": r.configured_model, "actual_model": r.actual_model,
             "route_type": r.route_type, "reason": r.reason} for r in rows]


@router.get("/api/chapters/{chapter_id}/context-packages")
async def list_context_packages(chapter_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import AgentContextPackage
    result = await db.execute(
        select(AgentContextPackage).where(AgentContextPackage.chapter_id == uuid.UUID(chapter_id))
        .order_by(AgentContextPackage.assembled_at.desc()).limit(20)
    )
    rows = result.scalars().all()
    return [{"id": str(r.id), "run_id": str(r.run_id), "attempt_no": r.attempt_no, "agent_role": r.agent_role,
             "provider": r.provider, "model": r.model, "publish_state": r.publish_state,
             "block_reason": r.block_reason, "assembled_at": r.assembled_at.isoformat()} for r in rows]


@router.get("/api/context-packages/{pkg_id}")
async def get_context_package_detail(pkg_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import AgentContextPackage
    result = await db.execute(select(AgentContextPackage).where(AgentContextPackage.id == uuid.UUID(pkg_id)))
    pkg = result.scalar_one_or_none()
    if not pkg:
        raise HTTPException(404, "Context package not found")
    return {
        "id": str(pkg.id), "run_id": str(pkg.run_id), "attempt_no": pkg.attempt_no,
        "agent_role": pkg.agent_role, "provider": pkg.provider, "model": pkg.model,
        "prompt_version": pkg.prompt_version, "prompt_template_hash": pkg.prompt_template_hash,
        "rendered_prompt_hash": pkg.rendered_prompt_hash, "assembly_manifest": pkg.assembly_manifest,
        "l4_entity_refs": pkg.l4_entity_refs, "assembled_token_estimate": pkg.assembled_token_estimate,
        "publish_state": pkg.publish_state, "block_reason": pkg.block_reason,
        "assembled_at": pkg.assembled_at.isoformat(),
    }


@router.post("/api/books/{book_id}/reference-samples")
async def upload_reference_sample(book_id: str, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    # Simplified: save compressed to /data/references/{book_id}/{uuid}.txt.gz
    import gzip
    import hashlib
    from pathlib import Path
    from app.models.tables import ReferenceSample
    book = await db.execute(select(Book).where(Book.id == uuid.UUID(book_id)))
    if not book.scalar_one_or_none():
        raise HTTPException(404, "Book not found")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 10MB)")
    sha = hashlib.sha256(content).hexdigest()
    sample_id = gen_uuid()
    out_dir = Path(f"/data/references/{book_id}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sample_id}.txt.gz"
    compressed = gzip.compress(content)
    out_path.write_bytes(compressed)
    sample = ReferenceSample(
        id=sample_id, book_id=uuid.UUID(book_id), original_filename=file.filename or "upload.txt",
        storage_path=str(out_path), content_sha256=sha, mime_type=file.content_type or "text/plain",
        original_size_bytes=len(content), compressed_size_bytes=len(compressed),
        character_count=len(content.decode("utf-8", errors="ignore")),
        status="ready", created_by="user",
    )
    db.add(sample)
    await db.commit()
    return {"sample_id": str(sample_id), "status": "ready"}


@router.get("/api/books/{book_id}/genre-profiles")
async def list_genre_profiles(book_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import GenreProfile
    result = await db.execute(
        select(GenreProfile).where(GenreProfile.book_id == uuid.UUID(book_id)).order_by(GenreProfile.version.desc())
    )
    rows = result.scalars().all()
    return [{"id": str(r.id), "version": r.version, "status": r.status, "created_at": r.created_at.isoformat()} for r in rows]


@router.post("/api/research-sessions/{session_id}/approve")
async def approve_research_session(session_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import ResearchSession
    result = await db.execute(select(ResearchSession).where(ResearchSession.id == uuid.UUID(session_id)))
    sess = result.scalar_one_or_none()
    if not sess:
        raise HTTPException(404, "Research session not found")
    if sess.status not in ("suggested", "queued"):
        raise HTTPException(400, f"Cannot approve session in status {sess.status}")
    sess.status = "approved"
    sess.approved_by = "user"
    sess.approved_at = datetime.now(timezone.utc)
    await db.commit()
    return {"session_id": session_id, "status": "approved"}
