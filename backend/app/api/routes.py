"""FastAPI routes - all endpoints per §附录B."""
import uuid
import json
import asyncio
from datetime import datetime, timezone
from fastapi import Request, APIRouter, HTTPException, Depends, WebSocket, WebSocketDisconnect, UploadFile, File, Form
from fastapi.responses import FileResponse
from pathlib import Path
from sqlalchemy import select, update, func, delete, text, bindparam
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

_DATA_ROOT = Path(os.environ.get("NOVELFORGE_DATA_DIR", "/app/data")).resolve()
_REFERENCE_ROOT = Path(os.environ.get("NOVELFORGE_REFERENCE_DIR", "/data/references")).resolve()
_ALLOWED_DATA_ROOTS = (_DATA_ROOT, _REFERENCE_ROOT)


def gen_uuid():
    return uuid.uuid4()


def _parse_uuid(value: str, *, field: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid {field}") from exc


def _safe_data_file(path: str | None, *, roots: tuple[Path, ...] | None = None) -> Path | None:
    """Only serve/delete files that live under configured data roots."""
    if not path:
        return None
    try:
        candidate = Path(path).resolve()
    except (OSError, ValueError):
        return None
    allowed = roots or _ALLOWED_DATA_ROOTS
    for root in allowed:
        try:
            candidate.relative_to(root)
            return candidate if candidate.is_file() else None
        except ValueError:
            continue
    return None


def _cleanup_cover_files(paths: list[str | None]) -> None:
    dirs: set[Path] = set()
    for raw in paths:
        safe = _safe_data_file(raw, roots=(_DATA_ROOT,))
        if safe is None:
            continue
        try:
            safe.unlink(missing_ok=True)
        except OSError:
            continue
        dirs.add(safe.parent)
    for directory in dirs:
        try:
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        except OSError:
            pass


async def _live_foreign_keys(db: AsyncSession) -> list[dict[str, str]]:
    rows = (
        await db.execute(
            text(
                """
                SELECT
                  tc.table_name AS child_table,
                  kcu.column_name AS child_column,
                  ccu.table_name AS parent_table,
                  ccu.column_name AS parent_column
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.table_schema = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = 'public'
                """
            )
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def _tables_with_column(db: AsyncSession, column_name: str) -> list[str]:
    rows = await db.execute(
        text(
            """
            SELECT table_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = :column_name
            ORDER BY table_name
            """
        ),
        {"column_name": column_name},
    )
    return [row[0] for row in rows]


async def _table_has_column(db: AsyncSession, table_name: str, column_name: str) -> bool:
    row = (
        await db.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = :table_name
                  AND column_name = :column_name
                LIMIT 1
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
    ).first()
    return row is not None


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
# Live is always up. Ready is driven by app.main lifespan readiness flag
# (provider + bindings + DB). See app.main.get_readiness().
@router.get("/health/live")
@router.get("/api/health/live")
async def health_live():
    return {"status": "alive"}


@router.get("/health/ready")
@router.get("/api/health/ready")
async def health_ready():
    from app.main import get_readiness

    ready, detail = get_readiness()
    if not ready:
        raise HTTPException(503, detail={"status": "not_ready", "detail": detail})
    return {"status": "ready", "detail": detail}


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


@router.delete("/api/books/{book_id}")
async def delete_book(book_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a book and its book-scoped records, including generated covers.

    Ownership and delete order are derived from live PostgreSQL foreign keys so
    legacy constraints that are missing from the ORM still block unsafe deletes
    instead of leaking a 500.
    """
    bid = _parse_uuid(book_id, field="book id")

    book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one_or_none()
    if not book:
        raise HTTPException(404, "Book not found")

    cover_paths = [book.cover_path, book.cover_thumb_path]
    fks = await _live_foreign_keys(db)
    owned: dict[str, set[Any]] = {"books": {bid}}

    # Seed ownership from every live table that still carries book_id, even if
    # that table is not declared in the current ORM metadata.
    for table_name in await _tables_with_column(db, "book_id"):
        if table_name == "books" or not await _table_has_column(db, table_name, "id"):
            continue
        rows = await db.execute(
            text(f'SELECT id FROM "{table_name}" WHERE book_id = :bid'),
            {"bid": bid},
        )
        ids = {row[0] for row in rows}
        if ids:
            owned.setdefault(table_name, set()).update(ids)

    # Expand ownership through live FK edges for indirect dependents such as
    # import artifacts, step runs, and research evidence.
    changed = True
    while changed:
        changed = False
        for edge in fks:
            parent_ids = owned.get(edge["parent_table"])
            if not parent_ids:
                continue
            child_table = edge["child_table"]
            if not await _table_has_column(db, child_table, "id"):
                continue
            rows = await db.execute(
                text(
                    f'SELECT id FROM "{child_table}" '
                    f'WHERE "{edge["child_column"]}" IN :parent_ids'
                ).bindparams(bindparam("parent_ids", expanding=True)),
                {"parent_ids": list(parent_ids)},
            )
            ids = {row[0] for row in rows}
            if not ids:
                continue
            before = len(owned.setdefault(child_table, set()))
            owned[child_table].update(ids)
            changed = changed or len(owned[child_table]) != before

    # Break known cycles / reverse refs before child-first deletes.
    if owned.get("chapters") and await _table_has_column(db, "chapters", "active_run_id"):
        await db.execute(
            text('UPDATE chapters SET active_run_id = NULL WHERE id IN :ids').bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": list(owned["chapters"])},
        )
    if await _table_has_column(db, "chapter_tasks", "run_id"):
        await db.execute(
            text('UPDATE chapter_tasks SET run_id = NULL WHERE book_id = :bid'),
            {"bid": bid},
        )
    if owned.get("chapter_runs") and await _table_has_column(db, "chapter_runs", "resume_from_run_id"):
        await db.execute(
            text('UPDATE chapter_runs SET resume_from_run_id = NULL WHERE id IN :ids').bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": list(owned["chapter_runs"])},
        )
    if owned.get("chapter_step_runs") and await _table_has_column(db, "chapter_step_runs", "reused_from_step_id"):
        await db.execute(
            text('UPDATE chapter_step_runs SET reused_from_step_id = NULL WHERE id IN :ids').bindparams(
                bindparam("ids", expanding=True)
            ),
            {"ids": list(owned["chapter_step_runs"])},
        )

    deleted_tables: set[str] = set()
    table_ids = {table: ids for table, ids in owned.items() if ids}
    child_to_parents: dict[str, set[str]] = {}
    for edge in fks:
        # active_run_id is explicitly nulled above before deleting chapter_runs;
        # it must not keep the otherwise removable chapter/run cycle blocked.
        if (
            edge["child_table"] == "chapters"
            and edge["child_column"] == "active_run_id"
            and edge["parent_table"] == "chapter_runs"
        ):
            continue
        child_to_parents.setdefault(edge["child_table"], set()).add(edge["parent_table"])

    for _ in range(len(table_ids) + 2):
        progress = False
        for table_name, ids in list(table_ids.items()):
            if table_name == "books" or table_name in deleted_tables:
                continue
            # Delete a table only after every owned child table that has an FK
            # pointing at it has already been deleted.
            blocked = any(
                other != table_name
                and other not in deleted_tables
                and table_name in child_to_parents.get(other, set())
                for other in table_ids
            )
            if blocked:
                continue
            await db.execute(
                text(f'DELETE FROM "{table_name}" WHERE id IN :ids').bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": list(ids)},
            )
            deleted_tables.add(table_name)
            progress = True
        if not progress:
            break

    unresolved = sorted(
        name for name in table_ids if name not in deleted_tables and name != "books"
    )
    if unresolved:
        raise HTTPException(
            409,
            detail={"message": "Book has dependent records", "tables": unresolved},
        )

    await db.execute(delete(Book).where(Book.id == bid))
    # Commit the relational delete first. Cover files are cleaned only after a
    # successful commit so a later rollback cannot leave orphaned DB rows.
    await db.commit()
    _cleanup_cover_files(cover_paths)
    return {"deleted": True, "book_id": str(bid)}


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


@router.post("/api/books/{book_id}/generate-cover")
async def generate_book_cover(book_id: str, db: AsyncSession = Depends(get_db)):
    """Generate and persist an exact 320x480 PNG cover."""
    from app.cover_renderer import render_cover
    from app.services.library_service import stable_cover_style
    import hashlib

    bid = _parse_uuid(book_id, field="book id")
    book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one_or_none()
    if not book:
        raise HTTPException(404, detail={"message": "Book not found"})

    cover_dir = os.path.join(os.environ.get("NOVELFORGE_DATA_DIR", "/app/data"), "books", str(bid), "covers")
    cover_hash = hashlib.sha256(f"{bid}|{book.title}|{book.genre}|{book.logline}".encode()).hexdigest()[:16]
    cover_path = f"{cover_dir}/cover_{cover_hash}.png"
    thumb_path = f"{cover_dir}/thumb_{cover_hash}.png"
    render_cover(cover_path, title=book.title or "Untitled", genre=book.genre or "", logline=book.logline or "")
    render_cover(thumb_path, title=book.title or "Untitled", genre=book.genre or "", logline=book.logline or "", width=160, height=240)
    book.cover_path = cover_path
    book.cover_thumb_path = thumb_path
    book.cover_hash = cover_hash
    await db.commit()
    return {
        "status": "generated",
        "width": 320,
        "height": 480,
        "cover_url": f"/api/library/books/{bid}/cover",
        "thumb_url": f"/api/library/books/{bid}/cover?thumb=1",
        "cover_style": stable_cover_style(bid),
        "file_size": os.path.getsize(cover_path),
    }


@router.get("/api/library/books/{book_id}/cover")
async def get_book_cover(book_id: str, thumb: int = 0, db: AsyncSession = Depends(get_db)):
    bid = _parse_uuid(book_id, field="book id")
    book = (await db.execute(select(Book).where(Book.id == bid))).scalar_one_or_none()
    if not book:
        raise HTTPException(404, detail={"message": "Book not found"})
    path = book.cover_thumb_path if thumb else book.cover_path
    safe = _safe_data_file(path, roots=(_DATA_ROOT,))
    if safe is None:
        raise HTTPException(404, detail={"message": "Cover not generated"})
    return FileResponse(
        safe,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/api/books/{book_id}/outline")
async def get_book_outline(book_id: str, db: AsyncSession = Depends(get_db)):
    """B-13: Return outline nodes for a book (latest approved or latest version)."""
    bid = uuid.UUID(book_id)
    # Get latest approved version, fallback to latest
    version_result = await db.execute(
        select(OutlineVersion)
        .where(OutlineVersion.book_id == bid)
        .order_by(
            (OutlineVersion.status == "approved").desc(),
            OutlineVersion.version.desc(),
        )
        .limit(1)
    )
    latest_version = version_result.scalar_one_or_none()
    if not latest_version:
        return {"nodes": [], "outline_version_id": None, "version": None, "status": None}
    
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
        "nodes": [
            {
                "node_id": str(n.id),
                "chapter_no": n.chapter_no,
                "title": n.title,
                "goal": n.goal,
                "depends_on": n.depends_on,
                "required_beats": n.required_beats,
                "forbidden_outcomes": n.forbidden_outcomes,
            }
            for n in nodes
        ],
        "outline_version_id": str(latest_version.id),
        "version": latest_version.version,
        "status": latest_version.status,
    }


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

async def _ensure_outline_node(
    db: AsyncSession,
    bid: uuid.UUID,
    chapter_no: int,
    *,
    title: str | None = None,
    goal: str | None = None,
) -> OutlineNode:
    """Create a minimal outline node if missing so user can keep writing."""
    from app.models import OutlineVersion

    ov = (
        await db.execute(
            select(OutlineVersion)
            .where(OutlineVersion.book_id == bid, OutlineVersion.status == "approved")
            .order_by(OutlineVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not ov:
        ov = (
            await db.execute(
                select(OutlineVersion)
                .where(OutlineVersion.book_id == bid)
                .order_by(OutlineVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if not ov:
        raise HTTPException(400, "请先上传并批准大纲（大纲依赖页）")

    node = (
        await db.execute(
            select(OutlineNode).where(
                OutlineNode.book_id == bid,
                OutlineNode.outline_version_id == ov.id,
                OutlineNode.chapter_no == chapter_no,
            )
        )
    ).scalar_one_or_none()
    if node:
        return node

    prev = (
        await db.execute(
            select(OutlineNode).where(
                OutlineNode.book_id == bid,
                OutlineNode.outline_version_id == ov.id,
                OutlineNode.chapter_no == chapter_no - 1,
            )
        )
    ).scalar_one_or_none()
    depends = []
    if prev:
        depends = [{"node_id": str(prev.id), "chapter_no": prev.chapter_no, "required": True}]
    default_goal = goal or (
        f"承接第{chapter_no - 1}章，推进主线与人物状态；保持与已定稿一致。"
        if chapter_no > 1
        else "开篇建立设定与主角动机。"
    )
    node = OutlineNode(
        id=gen_uuid(),
        book_id=bid,
        outline_version_id=ov.id,
        node_type="chapter",
        volume_no=1,
        chapter_no=chapter_no,
        title=title or f"第{chapter_no}章",
        goal=default_goal,
        required_beats=[],
        forbidden_outcomes=[],
        involved_character_ids=[],
        plot_thread_ids=[],
        depends_on=depends,
        expected_state_changes=[],
    )
    db.add(node)
    await db.flush()
    return node



@router.post("/api/books/{book_id}/chapters/next/run")
async def run_next_chapter(book_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Primary write button: find next chapter_no, ensure outline node, enqueue run."""
    bid = uuid.UUID(book_id)
    max_outline = (
        await db.execute(
            select(func.coalesce(func.max(OutlineNode.chapter_no), 0)).where(OutlineNode.book_id == bid)
        )
    ).scalar() or 0
    max_final = (
        await db.execute(
            select(func.coalesce(func.max(Chapter.chapter_no), 0)).where(
                Chapter.book_id == bid,
                Chapter.status == ChapterState.FINALIZED.value,
            )
        )
    ).scalar() or 0
    max_any = (
        await db.execute(
            select(func.coalesce(func.max(Chapter.chapter_no), 0)).where(Chapter.book_id == bid)
        )
    ).scalar() or 0
    # next after highest finalized among real story chapters (ignore huge test nos > max_outline+50)
    story_max = max(max_outline, max_final if max_final <= max_outline + 20 else max_outline)
    if story_max == 0:
        next_no = 1
    else:
        next_no = int(story_max) + 1

    # if next already exists unfinished, run that; if finalized, keep +1
    existing = (
        await db.execute(
            select(Chapter).where(Chapter.book_id == bid, Chapter.chapter_no == next_no)
        )
    ).scalar_one_or_none()
    if existing and existing.status == ChapterState.FINALIZED.value:
        next_no = next_no + 1

    await _ensure_outline_node(db, bid, next_no)
    await db.commit()
    # reuse run_chapter
    return await run_chapter(book_id, next_no, request, db)


@router.post("/api/books/{book_id}/chapters/{chapter_no}/run")
async def run_chapter(
    book_id: str,
    chapter_no: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create ChapterRun + Outbox atomically (B-11 / INV-11). Redis is best-effort."""
    from app.models import ChapterRun, OutlineVersion
    from app.workers.outbox_dispatcher import create_run_and_outbox
    from app.engine.state_transition import transition_chapter, IllegalTransitionError, StateConflictError

    bid = uuid.UUID(book_id)
    request_id = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key") or str(gen_uuid())

    # Prefer approved outline version; fall back to latest
    ov = (
        await db.execute(
            select(OutlineVersion)
            .where(OutlineVersion.book_id == bid, OutlineVersion.status == "approved")
            .order_by(OutlineVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not ov:
        ov = (
            await db.execute(
                select(OutlineVersion)
                .where(OutlineVersion.book_id == bid)
                .order_by(OutlineVersion.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if not ov:
        raise HTTPException(404, "Outline version not found")

    node = (
        await db.execute(
            select(OutlineNode).where(
                OutlineNode.book_id == bid,
                OutlineNode.chapter_no == chapter_no,
                OutlineNode.outline_version_id == ov.id,
            )
        )
    ).scalar_one_or_none()
    if not node:
        # fallback without version filter for legacy data
        node = (
            await db.execute(
                select(OutlineNode).where(
                    OutlineNode.book_id == bid, OutlineNode.chapter_no == chapter_no
                )
            )
        ).scalar_one_or_none()
    if not node:
        # UX: allow continuing past short outlines by auto-creating next nodes
        node = await _ensure_outline_node(db, bid, chapter_no)

    # Idempotency: same chapter + request_id returns existing run
    existing_run = (
        await db.execute(
            select(ChapterRun).where(
                ChapterRun.chapter_id.in_(
                    select(Chapter.id).where(Chapter.book_id == bid, Chapter.chapter_no == chapter_no)
                ),
                ChapterRun.request_id == request_id,
            )
        )
    ).scalar_one_or_none()
    if existing_run:
        return {
            "chapter_id": str(existing_run.chapter_id),
            "run_id": str(existing_run.id),
            "status": existing_run.status,
            "chapter_no": chapter_no,
            "status_url": f"/api/chapter-runs/{existing_run.id}",
        }

    # Active run conflict (INV-04)
    chapter = (
        await db.execute(
            select(Chapter).where(Chapter.book_id == bid, Chapter.chapter_no == chapter_no)
        )
    ).scalar_one_or_none()
    if chapter:
        active = (
            await db.execute(
                select(ChapterRun).where(
                    ChapterRun.chapter_id == chapter.id,
                    ChapterRun.status.in_(
                        ("queued", "running", "paused", "waiting_dependency", "retryable")
                    ),
                )
            )
        ).scalar_one_or_none()
        if active:
            raise HTTPException(
                409,
                detail={
                    "message": "active run exists",
                    "existing_run_id": str(active.id),
                },
            )
        if chapter.status == ChapterState.FINALIZED.value:
            raise HTTPException(409, "Chapter already finalized; mode=normal cannot overwrite final")

    if not chapter:
        chapter = Chapter(
            id=gen_uuid(),
            book_id=bid,
            chapter_no=chapter_no,
            outline_node_id=node.id,
            status=ChapterState.QUEUED.value,
            title=node.title,
        )
        db.add(chapter)
        await db.flush()
    else:
        # transition to queued via service when not already queued
        if chapter.status != ChapterState.QUEUED.value:
            try:
                await transition_chapter(
                    chapter.id,
                    ChapterState.QUEUED.value,
                    reason="api run request",
                    actor="api",
                    db=db,
                )
            except (IllegalTransitionError, StateConflictError, ValueError) as e:
                # force for failed/needs_human/resource_blocked via state machine rules
                if chapter.status in (
                    ChapterState.FAILED.value,
                    ChapterState.NEEDS_HUMAN.value,
                    ChapterState.RESOURCE_BLOCKED.value,
                    ChapterState.BLOCKED_BY_DEPENDENCY.value,
                ):
                    await transition_chapter(
                        chapter.id,
                        ChapterState.QUEUED.value,
                        reason="api requeue",
                        actor="api",
                        db=db,
                    )
                else:
                    raise HTTPException(409, str(e))

    run = await create_run_and_outbox(
        db,
        book_id=bid,
        chapter_id=chapter.id,
        chapter_no=chapter_no,
        outline_version_id=ov.id if ov else node.outline_version_id,
        request_id=request_id,
        created_by="api",
    )
    chapter.active_run_id = run.id

    # Compat ChapterTask for old worker path
    existing_task = (
        await db.execute(
            select(ChapterTask)
            .where(ChapterTask.book_id == bid, ChapterTask.chapter_no == chapter_no)
            .order_by(ChapterTask.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_task:
        existing_task.status = ChapterState.QUEUED.value
        existing_task.lease_owner = None
        existing_task.lease_expires_at = None
        existing_task.heartbeat_at = None
        existing_task.last_error_code = None
        existing_task.last_error_detail = None
    else:
        db.add(
            ChapterTask(
                id=gen_uuid(),
                book_id=bid,
                chapter_no=chapter_no,
                status=ChapterState.QUEUED.value,
            )
        )

    await db.commit()

    # Best-effort immediate dispatch; outbox recovers on failure (INV-11)
    try:
        from app.workers.outbox_dispatcher import dispatch_once

        await dispatch_once(limit=5)
    except Exception:
        pass

    return {
        "chapter_id": str(chapter.id),
        "run_id": str(run.id),
        "status": "queued",
        "chapter_no": chapter_no,
        "status_url": f"/api/chapter-runs/{run.id}",
    }


@router.post("/api/chapters/{chapter_id}/pause")
async def pause_chapter(chapter_id: str, db: AsyncSession = Depends(get_db)):
    """B-12: collaborative pause via control_requested + needs_human for compat."""
    from app.models import ChapterRun
    from app.engine.state_transition import transition_chapter

    result = await db.execute(select(Chapter).where(Chapter.id == uuid.UUID(chapter_id)))
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    # Mark active run control_requested=pause
    if chapter.active_run_id:
        run = (
            await db.execute(select(ChapterRun).where(ChapterRun.id == chapter.active_run_id))
        ).scalar_one_or_none()
        if run and run.status in ("queued", "running", "retryable", "waiting_dependency"):
            run.control_requested = "pause"
    try:
        await transition_chapter(
            chapter.id,
            ChapterState.NEEDS_HUMAN.value,
            reason="api pause",
            actor="api",
            db=db,
        )
    except Exception as e:
        raise HTTPException(409, str(e))
    await db.flush()
    return {"chapter_id": str(chapter.id), "status": "paused"}


@router.post("/api/chapters/{chapter_id}/resume")
async def resume_chapter(chapter_id: str, db: AsyncSession = Depends(get_db)):
    """B-12: clear control, requeue chapter, write outbox for active/new run."""
    from app.models import ChapterRun
    from app.engine.state_transition import transition_chapter
    from app.workers.outbox_dispatcher import create_run_and_outbox

    result = await db.execute(select(Chapter).where(Chapter.id == uuid.UUID(chapter_id)))
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    try:
        await transition_chapter(
            chapter.id,
            ChapterState.QUEUED.value,
            reason="api resume",
            actor="api",
            db=db,
        )
    except Exception as e:
        raise HTTPException(409, str(e))

    run = None
    if chapter.active_run_id:
        run = (
            await db.execute(select(ChapterRun).where(ChapterRun.id == chapter.active_run_id))
        ).scalar_one_or_none()
        if run and run.status in ("paused", "needs_human", "retryable", "failed", "waiting_dependency"):
            run.status = "queued"
            run.control_requested = "none"
            run.lease_owner = None
            run.lease_expires_at = None
            # ensure outbox
            from app.models import ChapterDispatchOutbox
            from datetime import datetime, timezone
            dedupe = f"dispatch:{run.id}:resume:{uuid.uuid4().hex[:8]}"
            db.add(
                ChapterDispatchOutbox(
                    id=gen_uuid(),
                    chapter_run_id=run.id,
                    dedupe_key=dedupe,
                    event_type="dispatch_chapter_run",
                    payload={
                        "chapter_id": str(chapter.id),
                        "book_id": str(chapter.book_id),
                        "chapter_no": chapter.chapter_no,
                        "run_id": str(run.id),
                        "job_id": f"chapter-run:{run.id}:{dedupe}",
                    },
                    status="pending",
                    available_at=datetime.now(timezone.utc),
                )
            )
    await db.commit()
    try:
        from app.workers.outbox_dispatcher import dispatch_once
        await dispatch_once(limit=5)
    except Exception:
        pass
    return {
        "chapter_id": str(chapter.id),
        "status": "queued",
        "run_id": str(run.id) if run else None,
    }


# ---- Chapter-level routes (static first, then parameterized) ----

@router.get("/api/chapters/needs-human")
async def list_needs_human_chapters(db: AsyncSession = Depends(get_db)):
    """PR-07: global needs-human queue across all books."""
    from app.models.tables import Chapter, ChapterRun, Book
    result = await db.execute(
        select(Chapter, Book.title.label("book_title"))
        .join(Book, Chapter.book_id == Book.id)
        .where(Chapter.status.in_(("needs_human", "paused", "control_requested")))
        .order_by(Chapter.updated_at.desc())
        .limit(50)
    )
    rows = result.all()
    out = []
    for ch, book_title in rows:
        run = None
        if ch.active_run_id:
            run = (await db.execute(select(ChapterRun).where(ChapterRun.id == ch.active_run_id))).scalar_one_or_none()
        out.append({
            "chapter_id": str(ch.id),
            "book_id": str(ch.book_id),
            "book_title": book_title or "未命名",
            "chapter_no": ch.chapter_no,
            "status": ch.status,
            "run_id": str(ch.active_run_id) if ch.active_run_id else None,
            "run_status": run.status if run else None,
            "current_step": run.current_step if run else None,
            "error_code": run.error_code if run else None,
            "error_detail": run.error_detail if run else None,
            "updated_at": ch.updated_at.isoformat() if ch.updated_at else None,
        })
    return {"chapters": out, "count": len(out)}


@router.get("/api/chapters/{chapter_id}")
async def get_chapter(chapter_id: str, db: AsyncSession = Depends(get_db)):
    """B-13 / INV-02: default read returns finalized content only."""
    result = await db.execute(select(Chapter).where(Chapter.id == uuid.UUID(chapter_id)))
    chapter = result.scalar_one_or_none()
    if not chapter:
        raise HTTPException(404, "Chapter not found")
    version = None
    if chapter.finalized_version is not None:
        version = (
            await db.execute(
                select(ChapterVersion).where(
                    ChapterVersion.chapter_id == chapter.id,
                    ChapterVersion.version == chapter.finalized_version,
                )
            )
        ).scalar_one_or_none()
    return {
        "chapter_id": str(chapter.id),
        "chapter_no": chapter.chapter_no,
        "status": chapter.status,
        "title": chapter.title,
        "finalized_version": chapter.finalized_version,
        "content": version.content if version else None,
        "word_count": version.word_count if version else 0,
    }


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

@router.get("/api/chapter-runs/{run_id}")
async def get_chapter_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """PR-07: ChapterRun detail for UI (status vs chapter.status)."""
    from app.models.tables import ChapterRun, ChapterStepRun, Chapter
    rid = uuid.UUID(run_id)
    run = (await db.execute(select(ChapterRun).where(ChapterRun.id == rid))).scalar_one_or_none()
    if not run:
        raise HTTPException(404, "run not found")
    ch = (await db.execute(select(Chapter).where(Chapter.id == run.chapter_id))).scalar_one_or_none()
    steps = (
        await db.execute(
            select(ChapterStepRun)
            .where(ChapterStepRun.chapter_run_id == rid)
            .order_by(ChapterStepRun.created_at.asc())
        )
    ).scalars().all()
    return {
        "run_id": str(run.id),
        "book_id": str(run.book_id),
        "chapter_id": str(run.chapter_id),
        "chapter_no": ch.chapter_no if ch else None,
        "chapter_status": ch.status if ch else None,
        "run_status": run.status,
        "current_step": run.current_step,
        "control_requested": run.control_requested,
        "error_code": run.error_code,
        "error_detail": run.error_detail,
        "lease_owner": run.lease_owner,
        "lease_expires_at": run.lease_expires_at.isoformat() if run.lease_expires_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "pipeline_version": run.pipeline_version,
        "steps": [
            {
                "step_run_id": str(s.id),
                "step_name": s.step_name,
                "step_key": s.step_key,
                "status": s.status,
                "attempt_no": s.attempt_no,
                "input_hash": s.input_hash,
                "error_code": s.error_code,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "finished_at": s.completed_at.isoformat() if getattr(s, "completed_at", None) else (
                    s.finished_at.isoformat() if getattr(s, "finished_at", None) else None
                ),
            }
            for s in steps
        ],
    }


@router.get("/api/chapters/{chapter_id}/runs")
async def list_chapter_runs(chapter_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import ChapterRun
    cid = uuid.UUID(chapter_id)
    runs = (
        await db.execute(
            select(ChapterRun)
            .where(ChapterRun.chapter_id == cid)
            .order_by(ChapterRun.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    return [
        {
            "run_id": str(r.id),
            "status": r.status,
            "current_step": r.current_step,
            "control_requested": r.control_requested,
            "error_code": r.error_code,
            "error_detail": r.error_detail,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in runs
    ]



@router.get("/api/chapters/{chapter_id}/needs-human")
async def chapter_needs_human_detail(chapter_id: str, db: AsyncSession = Depends(get_db)):
    """PR-07: evidence for needs_human — last transition reason + failed steps + active run."""
    from app.models.tables import Chapter, ChapterRun, ChapterStepRun, ChapterStateEvent
    cid = uuid.UUID(chapter_id)
    ch = (await db.execute(select(Chapter).where(Chapter.id == cid))).scalar_one_or_none()
    if not ch:
        raise HTTPException(404, "chapter not found")
    run = None
    if ch.active_run_id:
        run = (await db.execute(select(ChapterRun).where(ChapterRun.id == ch.active_run_id))).scalar_one_or_none()
    if not run:
        run = (
            await db.execute(
                select(ChapterRun)
                .where(ChapterRun.chapter_id == cid)
                .order_by(ChapterRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    steps = []
    if run:
        steps = (
            await db.execute(
                select(ChapterStepRun)
                .where(ChapterStepRun.chapter_run_id == run.id, ChapterStepRun.status.in_(("failed", "succeeded")))
                .order_by(ChapterStepRun.created_at.desc())
                .limit(30)
            )
        ).scalars().all()
    events = (
        await db.execute(
            select(ChapterStateEvent)
            .where(ChapterStateEvent.chapter_id == cid)
            .order_by(ChapterStateEvent.created_at.desc())
            .limit(10)
        )
    ).scalars().all()
    return {
        "chapter_id": str(ch.id),
        "chapter_no": ch.chapter_no,
        "status": ch.status,
        "last_transition_reason": getattr(ch, "last_transition_reason", None),
        "active_run_id": str(ch.active_run_id) if ch.active_run_id else None,
        "run": {
            "run_id": str(run.id),
            "status": run.status,
            "current_step": run.current_step,
            "error_code": run.error_code,
            "error_detail": run.error_detail,
            "control_requested": run.control_requested,
        } if run else None,
        "recent_steps": [
            {
                "step_name": s.step_name,
                "step_key": s.step_key,
                "status": s.status,
                "error_code": s.error_code,
                "error_detail": s.error_detail if hasattr(s, "error_detail") else None,
            }
            for s in steps
        ],
        "recent_state_events": [
            {
                "from_state": e.from_state,
                "to_state": e.to_state,
                "reason": e.reason,
                "actor": e.actor,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }


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
async def upload_reference_sample(
    book_id: str,
    file: UploadFile = File(...),
    genre_hint: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Upload reference text to shared /data/references volume (gzip)."""
    import gzip
    import hashlib
    from pathlib import Path
    from app.models.tables import ReferenceSample
    from app.engine.file_extract import extract_text

    book = await db.execute(select(Book).where(Book.id == uuid.UUID(book_id)))
    if not book.scalar_one_or_none():
        raise HTTPException(404, "Book not found")
    raw = await file.read()
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 10MB)")

    filename = file.filename or "upload.txt"
    # Prefer text extraction for docx/pdf; fallback to utf-8 decode
    try:
        text = extract_text(raw, filename=filename, content_type=file.content_type)
    except Exception:
        text = raw.decode("utf-8", errors="ignore")
    if not text or not text.strip():
        raise HTTPException(400, "Empty reference text")

    content_bytes = text.encode("utf-8")
    sha = hashlib.sha256(content_bytes).hexdigest()
    sample_id = gen_uuid()
    out_dir = Path(f"/data/references/{book_id}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sample_id}.txt.gz"
    compressed = gzip.compress(content_bytes)
    out_path.write_bytes(compressed)

    sample = ReferenceSample(
        id=sample_id,
        book_id=uuid.UUID(book_id),
        original_filename=filename,
        storage_path=str(out_path),
        content_sha256=sha,
        mime_type=file.content_type or "text/plain",
        original_size_bytes=len(content_bytes),
        compressed_size_bytes=len(compressed),
        character_count=len(text),
        genre_hint=genre_hint or None,
        status="ready",
        created_by="user",
    )
    db.add(sample)
    await db.commit()
    return {
        "sample_id": str(sample_id),
        "status": "ready",
        "character_count": len(text),
        "filename": filename,
    }


@router.get("/api/books/{book_id}/reference-samples")
async def list_reference_samples(book_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import ReferenceSample
    result = await db.execute(
        select(ReferenceSample)
        .where(ReferenceSample.book_id == uuid.UUID(book_id))
        .order_by(ReferenceSample.uploaded_at.desc())
    )
    rows = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "filename": r.original_filename,
            "status": r.status,
            "character_count": r.character_count,
            "genre_hint": r.genre_hint,
            "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
        }
        for r in rows
    ]


@router.delete("/api/books/{book_id}/reference-samples/{sample_id}")
async def delete_reference_sample(
    book_id: str, sample_id: str, db: AsyncSession = Depends(get_db)
):
    """Delete one uploaded reference sample and its compressed source file."""
    from app.models.tables import ReferenceSample

    bid = _parse_uuid(book_id, field="book id")
    sid = _parse_uuid(sample_id, field="sample id")
    sample = (
        await db.execute(
            select(ReferenceSample).where(
                ReferenceSample.id == sid,
                ReferenceSample.book_id == bid,
            )
        )
    ).scalar_one_or_none()
    if sample is None:
        raise HTTPException(404, "Reference sample not found")

    storage_path = sample.storage_path
    await db.delete(sample)
    await db.commit()
    safe = _safe_data_file(storage_path)
    if safe is not None:
        try:
            safe.unlink(missing_ok=True)
            if safe.parent.is_dir() and not any(safe.parent.iterdir()):
                safe.parent.rmdir()
        except OSError:
            # The database deletion is authoritative; a stale file is harmless
            # and can be cleaned up by the data maintenance job.
            pass
    return {"deleted": True, "sample_id": str(sid), "book_id": str(bid)}


@router.post("/api/books/{book_id}/reference-samples/{sample_id}/analyze")
async def analyze_reference_sample(
    book_id: str, sample_id: str, db: AsyncSession = Depends(get_db)
):
    """Analyze sample → StyleSanitizer → GenreProfile(pending_approval)."""
    import gzip
    from pathlib import Path
    from app.models.tables import ReferenceSample, GenreProfile, AgentRun
    from app.agents.reference_analyzer import run_reference_analyzer_with_system
    from app.engine.style_sanitizer import sanitize_genre_profile

    sample = (
        await db.execute(
            select(ReferenceSample).where(
                ReferenceSample.id == uuid.UUID(sample_id),
                ReferenceSample.book_id == uuid.UUID(book_id),
            )
        )
    ).scalar_one_or_none()
    if not sample:
        raise HTTPException(404, "Reference sample not found")

    path = Path(sample.storage_path)
    if not path.exists():
        raise HTTPException(404, f"Reference file missing on disk: {sample.storage_path}")
    text = gzip.decompress(path.read_bytes()).decode("utf-8", errors="ignore")

    sample.status = "analyzing"
    await db.commit()

    profile = await run_reference_analyzer_with_system(
        book_id=uuid.UUID(book_id),
        reference_text=text,
        genre_hint=sample.genre_hint,
    )
    if profile.get("error"):
        sample.status = "analyze_failed"
        await db.commit()
        raise HTTPException(502, f"Analyzer failed: {profile.get('error')}")

    report = sanitize_genre_profile(profile, text)
    status = "pending_approval" if report.get("passed") or report.get("manual_review_required") else "rejected"
    if report.get("manual_review_required"):
        status = "pending_approval"

    # version = max+1
    prev = await db.execute(
        select(GenreProfile)
        .where(GenreProfile.book_id == uuid.UUID(book_id))
        .order_by(GenreProfile.version.desc())
        .limit(1)
    )
    last = prev.scalar_one_or_none()
    version = (last.version + 1) if last else 1

    run_id_str = profile.pop("_analyzer_run_id", None)
    try:
        analyzer_run_id = uuid.UUID(run_id_str) if run_id_str else uuid.uuid4()
    except Exception:
        analyzer_run_id = uuid.uuid4()

    # Ensure analyzer_run_id exists in agent_runs (FK). If missing, create a stub.
    existing_run = (
        await db.execute(select(AgentRun).where(AgentRun.id == analyzer_run_id))
    ).scalar_one_or_none()
    if not existing_run:
        stub = AgentRun(
            id=analyzer_run_id,
            book_id=uuid.UUID(book_id),
            agent_role="reference_analyzer",
            status="completed",
            prompt_version="v7.4-ref",
            model_name="bound",
            idempotency_key=f"reference_analyzer:{sample_id}:{version}",
        )
        db.add(stub)
        await db.flush()

    gp = GenreProfile(
        id=gen_uuid(),
        book_id=uuid.UUID(book_id),
        version=version,
        status=status,
        narrative_person=profile.get("narrative_person"),
        pacing_profile=profile.get("pacing_profile") or {},
        technique_tags=profile.get("technique_tags") or [],
        lexical_tendency=profile.get("lexical_tendency") or {},
        content_intensity_notes=profile.get("content_intensity_notes"),
        prompt_injection_snippet=profile.get("prompt_injection_snippet")
        or ("（待编辑）" + "风格指导。" * 40)[:400],
        analyzer_run_id=analyzer_run_id,
        sanitizer_report=report,
    )
    db.add(gp)
    sample.status = "analyzed"
    await db.commit()
    return {
        "profile_id": str(gp.id),
        "version": version,
        "status": status,
        "sanitizer_report": report,
        "narrative_person": gp.narrative_person,
        "prompt_injection_snippet": gp.prompt_injection_snippet[:120],
    }


@router.get("/api/books/{book_id}/genre-profiles")
async def list_genre_profiles(book_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import GenreProfile
    result = await db.execute(
        select(GenreProfile)
        .where(GenreProfile.book_id == uuid.UUID(book_id))
        .order_by(GenreProfile.version.desc())
    )
    rows = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "version": r.version,
            "status": r.status,
            "narrative_person": r.narrative_person,
            "technique_tags": r.technique_tags,
            "prompt_injection_snippet": r.prompt_injection_snippet,
            "sanitizer_report": r.sanitizer_report,
            "approved_by": r.approved_by,
            "approved_at": r.approved_at.isoformat() if r.approved_at else None,
            "created_at": None,
        }
        for r in rows
    ]


@router.get("/api/genre-profiles/{profile_id}")
async def get_genre_profile(profile_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import GenreProfile
    r = (
        await db.execute(
            select(GenreProfile).where(GenreProfile.id == uuid.UUID(profile_id))
        )
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Genre profile not found")
    return {
        "id": str(r.id),
        "book_id": str(r.book_id),
        "version": r.version,
        "status": r.status,
        "narrative_person": r.narrative_person,
        "pacing_profile": r.pacing_profile,
        "technique_tags": r.technique_tags,
        "lexical_tendency": r.lexical_tendency,
        "content_intensity_notes": r.content_intensity_notes,
        "prompt_injection_snippet": r.prompt_injection_snippet,
        "sanitizer_report": r.sanitizer_report,
        "approved_by": r.approved_by,
        "approved_at": r.approved_at.isoformat() if r.approved_at else None,
    }


@router.patch("/api/genre-profiles/{profile_id}")
async def edit_genre_profile(profile_id: str, payload: dict, db: AsyncSession = Depends(get_db)):
    from app.models.tables import GenreProfile
    r = (
        await db.execute(
            select(GenreProfile).where(GenreProfile.id == uuid.UUID(profile_id))
        )
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Genre profile not found")
    if r.status == "approved":
        raise HTTPException(400, "Cannot edit approved profile; create a new version")
    for key in (
        "narrative_person",
        "pacing_profile",
        "technique_tags",
        "lexical_tendency",
        "content_intensity_notes",
        "prompt_injection_snippet",
    ):
        if key in payload and payload[key] is not None:
            setattr(r, key, payload[key])
    r.status = "pending_approval"
    await db.commit()
    return {"id": profile_id, "status": r.status}


@router.post("/api/genre-profiles/{profile_id}/approve")
async def approve_genre_profile(profile_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import GenreProfile
    r = (
        await db.execute(
            select(GenreProfile).where(GenreProfile.id == uuid.UUID(profile_id))
        )
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Genre profile not found")
    if r.status not in ("pending_approval", "suggested", "ready"):
        raise HTTPException(400, f"Cannot approve status={r.status}")
    # demote previous approved for same book
    prevs = await db.execute(
        select(GenreProfile).where(
            GenreProfile.book_id == r.book_id,
            GenreProfile.status == "approved",
            GenreProfile.id != r.id,
        )
    )
    for p in prevs.scalars().all():
        p.status = "superseded"
    r.status = "approved"
    r.approved_by = "user"
    r.approved_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": profile_id, "status": "approved", "version": r.version}


@router.post("/api/books/{book_id}/research-sessions")
async def create_research_session(book_id: str, payload: dict, db: AsyncSession = Depends(get_db)):
    """Create a research session + optional plan/search/synthesize pipeline."""
    from app.models.tables import ResearchSession, ExternalResearchEvidence, AgentRun
    from app.engine.safe_fetcher import safe_fetch, NullSearchProvider, DuckDuckGoLiteProvider

    book = await db.execute(select(Book).where(Book.id == uuid.UUID(book_id)))
    if not book.scalar_one_or_none():
        raise HTTPException(404, "Book not found")

    topic = (payload.get("topic") or payload.get("requested_topic") or "").strip()
    if not topic:
        raise HTTPException(400, "topic required")
    chapter_id = payload.get("chapter_id")
    urls = payload.get("urls") or []  # optional manual URLs
    do_search = bool(payload.get("search", True))
    max_results = int(payload.get("max_results") or 5)

    sess = ResearchSession(
        id=gen_uuid(),
        book_id=uuid.UUID(book_id),
        chapter_id=uuid.UUID(chapter_id) if chapter_id else None,
        outline_node_id=None,
        trigger_type=payload.get("trigger_type") or "manual",
        status="planning",
        requested_topic=topic,
    )
    db.add(sess)
    await db.flush()

    # Plan via call_agent
    plan_run_id = None
    try:
        from app.agents.caller import call_agent
        run, publishable, meta = await call_agent(
            book_id=uuid.UUID(book_id),
            agent_role="query_planner",
            user_content=json.dumps(
                {
                    "task": "research_plan",
                    "topic": topic,
                    "instructions": "产出调研计划 JSON: queries[], key_questions[], notes",
                },
                ensure_ascii=False,
            ),
            chapter_id=uuid.UUID(chapter_id) if chapter_id else None,
        )
        plan_run_id = run.id if run else None
        plan = publishable if isinstance(publishable, dict) else {}
    except Exception as e:
        plan = {"error": str(e), "queries": [topic]}
        plan_run_id = None

    sess.plan_run_id = plan_run_id
    sess.status = "searching"
    await db.commit()

    queries = []
    if isinstance(plan, dict):
        queries = plan.get("queries") or []
    if not queries:
        queries = [topic]

    provider = DuckDuckGoLiteProvider() if do_search else NullSearchProvider()
    hits: list[dict] = []
    for q in queries[:5]:
        try:
            hits.extend(await provider.search(q, max_results=max_results))
        except Exception:
            continue
    # merge manual urls
    for u in urls[:10]:
        hits.append({"url": u, "title": u, "snippet": ""})

    # de-dupe by url
    seen = set()
    uniq = []
    for h in hits:
        u = h.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        uniq.append(h)

    evidence_ids = []
    synthesis_run_id = uuid.uuid4()
    # stub synthesis run for FK
    stub = AgentRun(
        id=synthesis_run_id,
        book_id=uuid.UUID(book_id),
        agent_role="research_synth",
        status="running",
        prompt_version="v7.4-research",
        model_name="bound",
        idempotency_key=f"research_synth:{sess.id}",
    )
    db.add(stub)
    await db.flush()

    for h in uniq[:8]:
        url = h["url"]
        try:
            page = await safe_fetch(url)
            summary_src = (page.get("text") or h.get("snippet") or "")[:1500]
            title = page.get("title") or h.get("title")
            domain = page.get("domain") or ""
            content_hash = page.get("content_hash")
        except Exception as e:
            summary_src = f"[fetch_failed] {e}; snippet={h.get('snippet','')[:400]}"
            title = h.get("title")
            domain = urlparse_host(url)
            content_hash = None

        # synthesize short summary via call_agent (best-effort)
        summary = summary_src[:800]
        try:
            from app.agents.caller import call_agent
            run, pub, meta = await call_agent(
                book_id=uuid.UUID(book_id),
                agent_role="query_planner",
                user_content=json.dumps(
                    {
                        "task": "summarize_untrusted_web",
                        "topic": topic,
                        "url": url,
                        "content": f"<UNTRUSTED_WEB_CONTENT>\n{summary_src[:6000]}\n</UNTRUSTED_WEB_CONTENT>",
                        "instructions": "用中文归纳要点，不照抄长句，输出纯文本摘要。",
                    },
                    ensure_ascii=False,
                ),
            )
            if isinstance(pub, str) and pub.strip():
                summary = pub.strip()[:1200]
            elif isinstance(pub, dict):
                summary = json.dumps(pub, ensure_ascii=False)[:1200]
            if run:
                synthesis_run_id = run.id
        except Exception:
            pass

        ev = ExternalResearchEvidence(
            id=gen_uuid(),
            research_session_id=sess.id,
            book_id=uuid.UUID(book_id),
            chapter_id=uuid.UUID(chapter_id) if chapter_id else None,
            query=topic,
            source_url=url[:2000],
            source_domain=(domain or "")[:500],
            source_title=(title or "")[:500] if title else None,
            summary=summary,
            source_content_hash=content_hash,
            fetched_at=datetime.now(timezone.utc),
            trust_tier="web",
            relevance="unknown",
            confidence=0.5,
            status="suggested",
            evidence_source="external_research",
            source_run_id=synthesis_run_id,
        )
        db.add(ev)
        evidence_ids.append(str(ev.id))

    sess.status = "suggested"
    sess.synthesis_run_id = synthesis_run_id
    await db.commit()
    return {
        "session_id": str(sess.id),
        "status": sess.status,
        "topic": topic,
        "plan": plan,
        "evidence_count": len(evidence_ids),
        "evidence_ids": evidence_ids,
    }


def urlparse_host(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


@router.get("/api/books/{book_id}/research-sessions")
async def list_research_sessions(book_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import ResearchSession
    result = await db.execute(
        select(ResearchSession)
        .where(ResearchSession.book_id == uuid.UUID(book_id))
        .order_by(ResearchSession.id.desc())
        .limit(50)
    )
    rows = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "status": r.status,
            "requested_topic": r.requested_topic,
            "trigger_type": r.trigger_type,
            "approved_by": r.approved_by,
            "approved_at": r.approved_at.isoformat() if r.approved_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in rows
    ]


@router.get("/api/research-sessions/{session_id}")
async def get_research_session(session_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import ResearchSession, ExternalResearchEvidence
    sess = (
        await db.execute(
            select(ResearchSession).where(ResearchSession.id == uuid.UUID(session_id))
        )
    ).scalar_one_or_none()
    if not sess:
        raise HTTPException(404, "Research session not found")
    evs = (
        await db.execute(
            select(ExternalResearchEvidence).where(
                ExternalResearchEvidence.research_session_id == sess.id
            )
        )
    ).scalars().all()
    return {
        "id": str(sess.id),
        "book_id": str(sess.book_id),
        "status": sess.status,
        "requested_topic": sess.requested_topic,
        "evidence": [
            {
                "id": str(e.id),
                "source_url": e.source_url,
                "source_title": e.source_title,
                "summary": e.summary,
                "status": e.status,
                "confidence": e.confidence,
                "trust_tier": e.trust_tier,
            }
            for e in evs
        ],
    }


@router.post("/api/research-sessions/{session_id}/approve")
async def approve_research_session(session_id: str, db: AsyncSession = Depends(get_db)):
    from app.models.tables import ResearchSession, ExternalResearchEvidence
    result = await db.execute(
        select(ResearchSession).where(ResearchSession.id == uuid.UUID(session_id))
    )
    sess = result.scalar_one_or_none()
    if not sess:
        raise HTTPException(404, "Research session not found")
    if sess.status not in ("suggested", "queued", "searching", "planning"):
        raise HTTPException(400, f"Cannot approve session in status {sess.status}")
    sess.status = "approved"
    sess.approved_by = "user"
    sess.approved_at = datetime.now(timezone.utc)
    sess.completed_at = datetime.now(timezone.utc)
    # mark evidence approved
    evs = await db.execute(
        select(ExternalResearchEvidence).where(
            ExternalResearchEvidence.research_session_id == sess.id
        )
    )
    for e in evs.scalars().all():
        e.status = "approved"
    await db.commit()
    return {"session_id": session_id, "status": "approved"}


@router.get("/api/context-packages/{pkg_id}/prompt-preview")
async def preview_context_prompt(pkg_id: str, db: AsyncSession = Depends(get_db)):
    """Rebuild a human-readable prompt preview from stored package metadata.
    Full rendered prompt is not stored (only hashes) — reconstruct skeleton.
    """
    from app.models.tables import AgentContextPackage
    from app.prompts import PROMPTS
    pkg = (
        await db.execute(
            select(AgentContextPackage).where(AgentContextPackage.id == uuid.UUID(pkg_id))
        )
    ).scalar_one_or_none()
    if not pkg:
        raise HTTPException(404, "Context package not found")
    role = pkg.agent_role
    system = PROMPTS.get(role, {}).get("system_prompt", f"[no system prompt for {role}]")
    manifest = pkg.assembly_manifest or {}
    preview = {
        "package_id": str(pkg.id),
        "agent_role": role,
        "provider": pkg.provider,
        "model": pkg.model,
        "prompt_version": pkg.prompt_version,
        "prompt_template_hash": pkg.prompt_template_hash,
        "rendered_prompt_hash": pkg.rendered_prompt_hash,
        "system_prompt_preview": system[:2000],
        "assembly_manifest": manifest,
        "l4_entity_refs": pkg.l4_entity_refs,
        "l1_ledger_refs": getattr(pkg, "l1_ledger_refs", None),
        "l2_summary_refs": getattr(pkg, "l2_summary_refs", None),
        "l3_summary_refs": getattr(pkg, "l3_summary_refs", None),
        "story_evidence_refs": getattr(pkg, "story_evidence_refs", None),
        "external_evidence_refs": getattr(pkg, "external_evidence_refs", None),
        "genre_profile_ref": str(pkg.genre_profile_ref) if pkg.genre_profile_ref else None,
        "assembled_token_estimate": pkg.assembled_token_estimate,
        "note": "Full rendered user prompt is not retained (hash only). Manifest + refs are the reconstruction surface.",
    }
    return preview


@router.get("/api/books/{book_id}/chapters")
async def list_chapters(book_id: str, db: AsyncSession = Depends(get_db)):
    """List chapters for Context Inspector jump links."""
    from app.models import Chapter
    result = await db.execute(
        select(Chapter)
        .where(Chapter.book_id == uuid.UUID(book_id))
        .order_by(Chapter.chapter_no)
    )
    rows = result.scalars().all()
    return [
        {
            "chapter_id": str(c.id),
            "chapter_no": c.chapter_no,
            "status": c.status,
            "title": c.title,
            "word_count": getattr(c, "word_count", None) or 0,
        }
        for c in rows
    ]


@router.get("/api/books/{book_id}/export")
async def export_book(book_id: str, db: AsyncSession = Depends(get_db)):
    """Export finalized (or latest) chapter texts as a single plain-text novel."""
    from fastapi.responses import PlainTextResponse

    book = (
        await db.execute(select(Book).where(Book.id == uuid.UUID(book_id)))
    ).scalar_one_or_none()
    if not book:
        raise HTTPException(404, "Book not found")

    chapters = (
        await db.execute(
            select(Chapter)
            .where(Chapter.book_id == book.id)
            .order_by(Chapter.chapter_no)
        )
    ).scalars().all()

    parts: list[str] = [f"# {book.title}", ""]
    exported = 0
    for ch in chapters:
        # B-13 / INV-02: export ONLY finalized chapters with final pointer
        if ch.status != "finalized" or ch.finalized_version is None:
            continue
        version = (
            await db.execute(
                select(ChapterVersion).where(
                    ChapterVersion.chapter_id == ch.id,
                    ChapterVersion.version == ch.finalized_version,
                )
            )
        ).scalar_one_or_none()
        content = (version.content if version else None) or ""
        if not content.strip():
            continue
        title = ch.title or f"第{ch.chapter_no}章"
        parts.append(f"## 第{ch.chapter_no}章 · {title}")
        parts.append("")
        parts.append(content.strip())
        parts.append("")
        parts.append("")
        exported += 1

    if exported == 0:
        raise HTTPException(404, "No chapter content available to export")

    body = "\n".join(parts)
    # Content-Disposition must be latin-1 safe; use RFC5987 filename*
    safe_name = "".join(c if (c.isascii() and (c.isalnum() or c in "-_")) else "_" for c in (book.title or "novel"))[:40]
    if not safe_name or set(safe_name) == {"_"}:
        safe_name = "novel"
    from urllib.parse import quote
    encoded = quote(f"{book.title or 'novel'}.txt")
    headers = {
        "Content-Disposition": f"attachment; filename=\"{safe_name}.txt\"; filename*=UTF-8''{encoded}",
    }
    return PlainTextResponse(content=body, media_type="text/plain; charset=utf-8", headers=headers)
