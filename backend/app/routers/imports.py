"""Import sessions API — upload + extract skeleton (no formal Book until commit)."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.engine.document_blocks import candidate_sanitize, extract_file, sha256_bytes
from app.models.tables import (
    BookSource,
    ImportArtifact,
    ImportConflict,
    ImportSession,
    ImportSessionEvent,
)

router = APIRouter(prefix="/api/import-sessions", tags=["import"])

UPLOAD_ROOT = Path(os.environ.get("NOVELFORGE_UPLOAD_ROOT", "/app/data/imports"))


def _utcnow():
    return datetime.now(timezone.utc)


def gen_uuid():
    return uuid.uuid4()


async def _transition(db: AsyncSession, sess: ImportSession, to_status: str, step: str | None = None, detail: dict | None = None):
    ev = ImportSessionEvent(
        id=gen_uuid(),
        import_session_id=sess.id,
        from_status=sess.status,
        to_status=to_status,
        step=step,
        detail=detail or {},
    )
    db.add(ev)
    sess.status = to_status
    sess.current_step = step
    sess.updated_at = _utcnow()


@router.post("")
async def create_import_session(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload source file → ImportSession (no Book). Extract text synchronously (no LLM)."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(400, "file too large (max 50MB)")

    sha = sha256_bytes(data)
    source_id = gen_uuid()
    session_id = gen_uuid()
    safe_name = (file.filename or "upload.bin").replace("/", "_")[:200]
    # Prefer mounted import dir; fall back to /tmp if not writable (container perms)
    roots = [UPLOAD_ROOT, Path("/tmp/novelforge_imports")]
    dest = None
    last_err: Exception | None = None
    for root in roots:
        try:
            root.mkdir(parents=True, exist_ok=True)
            candidate = root / str(session_id) / safe_name
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_bytes(data)
            dest = candidate
            break
        except OSError as e:
            last_err = e
            continue
    if dest is None:
        raise HTTPException(500, f"cannot write upload: {last_err}")

    source = BookSource(
        id=source_id,
        book_id=None,
        original_filename=safe_name,
        mime_type=file.content_type,
        file_size=len(data),
        sha256=sha,
        storage_path=str(dest),
        extractor_version="v1",
        uploaded_by="admin",
        legacy_import=False,
    )
    db.add(source)

    sess = ImportSession(
        id=session_id,
        status="uploaded",
        source_id=source_id,
        progress=0.05,
        current_step="uploaded",
        parser_version="v8.0",
        pipeline_version="v8.0",
    )
    db.add(sess)
    await db.flush()
    await _transition(db, sess, "uploaded", "uploaded", {"filename": safe_name, "sha256": sha})

    # Phase 1: extract blocks without LLM
    await _transition(db, sess, "extracting", "extracting")
    sess.progress = 0.2
    try:
        doc = extract_file(dest, safe_name, str(source_id))
        source.extracted_blocks_json = doc
        art = ImportArtifact(
            id=gen_uuid(),
            import_session_id=session_id,
            artifact_type="document_blocks",
            artifact_key="raw_blocks",
            version=1,
            status="ready",
            input_hash=sha,
            output_json=doc,
            source_refs=[],
        )
        db.add(art)

        # rule-only sanitize candidates
        await _transition(db, sess, "sanitizing", "sanitizing")
        sess.progress = 0.35
        cand = candidate_sanitize(doc.get("blocks") or [])
        db.add(
            ImportArtifact(
                id=gen_uuid(),
                import_session_id=session_id,
                artifact_type="sanitize_candidates",
                artifact_key="sanitize_v1",
                version=1,
                status="ready",
                output_json={"items": cand},
            )
        )

        # lightweight preview scaffold (no LLM entity extract yet)
        kept = [
            b
            for b, c in zip(doc.get("blocks") or [], cand)
            if c.get("action") != "exclude"
        ]
        headings = [b for b in kept if b.get("type") == "heading"]
        preview = {
            "title_guess": headings[0]["text"] if headings else Path(safe_name).stem,
            "block_count": len(doc.get("blocks") or []),
            "heading_count": len(headings),
            "sanitize_review_count": sum(1 for c in cand if c.get("action") == "review"),
            "note": "Phase1: 文本已提取。多阶段 LLM 抽取将在后续 worker 步骤填充人物/世界/大纲。",
            "headings_sample": [h["text"] for h in headings[:40]],
        }
        preview_hash = sha256_bytes(json.dumps(preview, ensure_ascii=False, sort_keys=True).encode())
        sess.preview_hash = preview_hash
        db.add(
            ImportArtifact(
                id=gen_uuid(),
                import_session_id=session_id,
                artifact_type="preview_scaffold",
                artifact_key="preview",
                version=1,
                status="ready",
                output_json=preview,
            )
        )

        # if many chatter candidates, open a non-blocking conflict
        review_n = sum(1 for c in cand if c.get("action") == "review")
        if review_n:
            db.add(
                ImportConflict(
                    id=gen_uuid(),
                    import_session_id=session_id,
                    code="CHATTER_CANDIDATES",
                    severity="warning",
                    entity_type="document_block",
                    message=f"检测到 {review_n} 处疑似 AI 对话残留，请在预览中确认清洗结果",
                    options=[
                        {"id": "review_later", "label": "稍后在预览中处理"},
                        {"id": "accept_rules", "label": "接受规则候选"},
                    ],
                    status="open",
                )
            )

        await _transition(db, sess, "preview_ready", "preview_ready")
        sess.progress = 0.5
        sess.primary_document_type = "mixed_book_proposal"
        sess.document_types = ["book_proposal"]
    except Exception as e:
        await _transition(db, sess, "failed", "extract_failed", {"error": str(e)})
        sess.error_code = "EXTRACT_FAILED"
        sess.error_detail = str(e)
        await db.commit()
        raise HTTPException(500, f"extract failed: {e}")

    await db.commit()
    return {
        "import_session_id": str(session_id),
        "status": sess.status,
        "progress": sess.progress,
        "preview_hash": sess.preview_hash,
        "source": {"id": str(source_id), "filename": safe_name, "sha256": sha, "size": len(data)},
    }


@router.get("/{session_id}")
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    sid = uuid.UUID(session_id)
    sess = (
        await db.execute(select(ImportSession).where(ImportSession.id == sid))
    ).scalar_one_or_none()
    if not sess:
        raise HTTPException(404, "not found")
    events = (
        await db.execute(
            select(ImportSessionEvent)
            .where(ImportSessionEvent.import_session_id == sid)
            .order_by(ImportSessionEvent.created_at)
        )
    ).scalars().all()
    return {
        "id": str(sess.id),
        "status": sess.status,
        "progress": sess.progress,
        "current_step": sess.current_step,
        "error_code": sess.error_code,
        "error_detail": sess.error_detail,
        "preview_hash": sess.preview_hash,
        "book_id": str(sess.book_id) if sess.book_id else None,
        "primary_document_type": sess.primary_document_type,
        "document_types": sess.document_types,
        "events": [
            {
                "from": e.from_status,
                "to": e.to_status,
                "step": e.step,
                "at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }


@router.get("/{session_id}/preview")
async def get_preview(session_id: str, db: AsyncSession = Depends(get_db)):
    sid = uuid.UUID(session_id)
    sess = (
        await db.execute(select(ImportSession).where(ImportSession.id == sid))
    ).scalar_one_or_none()
    if not sess:
        raise HTTPException(404, "not found")
    arts = (
        await db.execute(
            select(ImportArtifact)
            .where(ImportArtifact.import_session_id == sid)
            .order_by(ImportArtifact.created_at)
        )
    ).scalars().all()
    conflicts = (
        await db.execute(
            select(ImportConflict).where(ImportConflict.import_session_id == sid)
        )
    ).scalars().all()
    by_key = {a.artifact_key: a.output_json for a in arts}
    return {
        "import_session_id": str(sid),
        "status": sess.status,
        "preview_hash": sess.preview_hash,
        "preview": by_key.get("preview") or {},
        "sanitize": by_key.get("sanitize_v1") or {},
        "artifacts": [
            {
                "id": str(a.id),
                "type": a.artifact_type,
                "key": a.artifact_key,
                "version": a.version,
                "status": a.status,
            }
            for a in arts
        ],
        "conflicts": [
            {
                "conflict_id": str(c.id),
                "code": c.code,
                "severity": c.severity,
                "message": c.message,
                "options": c.options,
                "status": c.status,
                "selected_option_id": c.selected_option_id,
            }
            for c in conflicts
        ],
        "summary": {
            "作品信息": 1 if by_key.get("preview") else 0,
            "待确认冲突": sum(1 for c in conflicts if c.status == "open"),
            "说明": "完整人物/世界/卷纲抽取需 Phase2 LLM pipeline",
        },
    }


class ResolveBody(BaseModel):
    option_id: str


@router.post("/{session_id}/conflicts/{conflict_id}/resolve")
async def resolve_conflict(
    session_id: str,
    conflict_id: str,
    body: ResolveBody,
    db: AsyncSession = Depends(get_db),
):
    cid = uuid.UUID(conflict_id)
    c = (
        await db.execute(select(ImportConflict).where(ImportConflict.id == cid))
    ).scalar_one_or_none()
    if not c or str(c.import_session_id) != session_id:
        raise HTTPException(404, "conflict not found")
    c.selected_option_id = body.option_id
    c.status = "resolved"
    await db.commit()
    return {"status": "resolved", "conflict_id": conflict_id, "option_id": body.option_id}


class CommitBody(BaseModel):
    expected_preview_hash: str
    book_overrides: dict | None = None


@router.post("/{session_id}/commit")
async def commit_session(
    session_id: str,
    body: CommitBody,
    db: AsyncSession = Depends(get_db),
):
    """Phase1 commit: create minimal Book + BookProfile only after preview_ready.

    Full multi-entity atomic commit lands in Phase 3; this still forbids pre-create Book on upload.
    """
    from app.models.tables import Book, BookProfile

    sid = uuid.UUID(session_id)
    sess = (
        await db.execute(select(ImportSession).where(ImportSession.id == sid))
    ).scalar_one_or_none()
    if not sess:
        raise HTTPException(404, "not found")
    if sess.status not in ("preview_ready", "needs_human"):
        raise HTTPException(400, f"cannot commit from status={sess.status}")
    if body.expected_preview_hash != sess.preview_hash:
        raise HTTPException(status_code=409, detail={"code": "PREVIEW_STALE", "message": "preview hash mismatch"})

    blocking = (
        await db.execute(
            select(ImportConflict).where(
                ImportConflict.import_session_id == sid,
                ImportConflict.severity == "blocking",
                ImportConflict.status == "open",
            )
        )
    ).scalars().all()
    if blocking:
        raise HTTPException(400, f"{len(blocking)} blocking conflicts unresolved")

    if sess.book_id:
        return {"book_id": str(sess.book_id), "status": "already_committed"}

    await _transition(db, sess, "committing", "committing")
    art = (
        await db.execute(
            select(ImportArtifact).where(
                ImportArtifact.import_session_id == sid,
                ImportArtifact.artifact_key == "preview",
            )
        )
    ).scalar_one_or_none()
    preview = (art.output_json if art else {}) or {}
    overrides = body.book_overrides or {}
    title = overrides.get("title") or preview.get("title_guess") or "未命名小说"

    book = Book(
        id=gen_uuid(),
        title=title,
        description=overrides.get("description"),
        status="created",
        target_chapters=int(overrides.get("planned_chapters") or 500),
        planned_chapters=int(overrides.get("planned_chapters") or 500),
        lifecycle_status="draft",
        source_import_session_id=sid,
        tags=overrides.get("tags") or [],
        logline=overrides.get("logline"),
        last_activity_at=_utcnow(),
    )
    db.add(book)
    await db.flush()
    db.add(
        BookProfile(
            id=gen_uuid(),
            book_id=book.id,
            logline=book.logline,
            synopsis=None,
            genre=overrides.get("genre"),
        )
    )
    sess.book_id = book.id
    await _transition(db, sess, "completed", "completed")
    sess.progress = 1.0
    sess.completed_at = _utcnow()
    await db.commit()
    return {"book_id": str(book.id), "status": "completed", "import_session_id": str(sid)}


@router.post("/{session_id}/cancel")
async def cancel_session(session_id: str, db: AsyncSession = Depends(get_db)):
    sid = uuid.UUID(session_id)
    sess = (
        await db.execute(select(ImportSession).where(ImportSession.id == sid))
    ).scalar_one_or_none()
    if not sess:
        raise HTTPException(404, "not found")
    if sess.status == "completed":
        raise HTTPException(400, "already completed")
    await _transition(db, sess, "cancelled", "cancelled")
    await db.commit()
    return {"status": "cancelled"}
