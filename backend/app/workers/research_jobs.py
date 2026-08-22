"""v9.1 Research ARQ job (spec §20, §22).

The worker owns ALL task state:
    queued → running → load source → scrape → persist each document
    → update progress → check cancel_requested → completed / failed

The scraper is a stateless async generator; every DB write happens here.
Runs on the existing ARQ worker (ARQ_MAX_JOBS=1) — no new container.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session_factory
from app.models import ResearchDocument, ResearchSource, ResearchTask
from app.research.models import ResearchSourceConfig
from app.research.scraper import (
    MAX_DOCUMENTS_PER_TASK,
    ResearchScraper,
    ScrapeError,
)

logger = logging.getLogger("novelforge.research.jobs")

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _load_task_and_source(db, task_id: uuid.UUID) -> tuple[ResearchTask, ResearchSource] | None:
    task = (
        await db.execute(select(ResearchTask).where(ResearchTask.id == task_id))
    ).scalar_one_or_none()
    if task is None:
        logger.error("research task not found: %s", task_id)
        return None
    source = (
        await db.execute(select(ResearchSource).where(ResearchSource.id == task.source_id))
    ).scalar_one_or_none()
    if source is None:
        logger.error("research source missing for task %s", task_id)
        return None
    return task, source


def _source_config(source: ResearchSource) -> ResearchSourceConfig:
    extra = dict(source.config_json or {})
    return ResearchSourceConfig(
        code=source.code,
        name=source.name,
        base_url=source.base_url,
        chapter_list_selector=source.chapter_list_selector,
        title_selector=source.title_selector,
        content_selector=source.content_selector,
        pagination_selector=source.pagination_selector,
        encoding=source.encoding or "utf-8",
        rate_limit=source.rate_limit or 0.5,
        verification_status=source.verification_status or "experimental",
        extra=extra,
    )


async def _save_document(db, task: ResearchTask, doc, source_code: str) -> None:
    """Persist one scraped document with full content (spec §23).

    (task_id, source_url) dedupe: a re-run task never duplicates rows.
    """
    existing = (
        await db.execute(
            select(ResearchDocument).where(
                ResearchDocument.task_id == task.id,
                ResearchDocument.source_url == doc.source_url,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.title = doc.title
        existing.content = doc.content
        existing.content_hash = doc.content_hash
        existing.char_count = doc.char_count
        existing.ordinal = doc.ordinal
        return
    db.add(
        ResearchDocument(
            id=uuid.uuid4(),
            task_id=task.id,
            book_id=task.book_id,
            ordinal=doc.ordinal,
            title=doc.title,
            source_url=doc.source_url,
            content=doc.content,
            content_hash=doc.content_hash,
            char_count=doc.char_count,
            metadata_json={"source_code": source_code},
        )
    )


async def run_research_task(ctx, task_id: str) -> dict:
    """ARQ job entry: execute one research task end-to-end."""
    try:
        tid = uuid.UUID(str(task_id))
    except (ValueError, AttributeError):
        logger.error("research task id invalid: %r", task_id)
        return {"ok": False, "error": "invalid_task_id"}

    scraper = ResearchScraper()
    try:
        async with async_session_factory() as db:
            loaded = await _load_task_and_source(db, tid)
            if loaded is None:
                return {"ok": False, "error": "task_or_source_missing"}
            task, source = loaded

            if task.status in TERMINAL_STATUSES:
                logger.info("research task %s already terminal (%s), skip", tid, task.status)
                return {"ok": True, "skipped": True, "status": task.status}
            if not source.enabled:
                task.status = "failed"
                task.error_code = "SOURCE_DISABLED"
                task.error_detail = {"source": source.code}
                task.finished_at = _now()
                await db.commit()
                return {"ok": False, "error": "source_disabled"}

            task.status = "running"
            task.started_at = _now()
            task.progress = 0
            task.discovered_count = 0
            task.completed_count = 0
            task.current_url = task.target_url
            await db.commit()
            cfg = _source_config(source)
            source_code = source.code
            target_url = task.target_url

        # scrape + persist stream (fresh session per document commit)
        completed = 0
        discovered = 0
        async for doc in scraper.scrape(source=cfg, start_url=target_url):
            discovered += 1
            async with async_session_factory() as db:
                loaded = await _load_task_and_source(db, tid)
                if loaded is None:
                    return {"ok": False, "error": "task_or_source_missing"}
                task, _source = loaded
                if task.status == "cancel_requested":
                    task.status = "cancelled"
                    task.finished_at = _now()
                    await db.commit()
                    logger.info("research task cancelled mid-stream: %s", tid)
                    return {"ok": True, "status": "cancelled", "completed": completed}
                await _save_document(db, task, doc, source_code)
                completed += 1
                task.completed_count = completed
                task.discovered_count = max(discovered, completed)
                task.current_url = doc.source_url
                # progress: completed vs scraper hard cap, capped at 95 until done
                task.progress = min(95, int(completed * 100 / MAX_DOCUMENTS_PER_TASK))
                await db.commit()

        async with async_session_factory() as db:
            loaded = await _load_task_and_source(db, tid)
            if loaded is None:
                return {"ok": False, "error": "task_or_source_missing"}
            task, _source = loaded
            if task.status == "cancel_requested":
                task.status = "cancelled"
                task.finished_at = _now()
                await db.commit()
                return {"ok": True, "status": "cancelled", "completed": completed}
            task.status = "completed"
            task.progress = 100
            task.completed_count = completed
            task.discovered_count = max(discovered, completed)
            task.current_url = None
            task.finished_at = _now()
            await db.commit()

        logger.info("research task completed: %s documents=%s", tid, completed)
        return {"ok": True, "status": "completed", "completed": completed}

    except ScrapeError as e:
        logger.warning("research task failed %s: %s", task_id, e)
        async with async_session_factory() as db:
            loaded = await _load_task_and_source(db, tid)
            if loaded is not None:
                task, _src = loaded
                task.status = "failed"
                task.error_code = e.code
                task.error_detail = {"detail": e.detail[:2000]}
                task.finished_at = _now()
                await db.commit()
        return {"ok": False, "error": e.code, "detail": e.detail}

    except Exception as e:
        logger.exception("research task crashed: %s", task_id)
        async with async_session_factory() as db:
            loaded = await _load_task_and_source(db, tid)
            if loaded is not None:
                task, _src = loaded
                task.status = "failed"
                task.error_code = "WORKER_CRASH"
                task.error_detail = {"detail": str(e)[:2000]}
                task.finished_at = _now()
                await db.commit()
        return {"ok": False, "error": "WORKER_CRASH", "detail": str(e)[:500]}
    finally:
        await scraper.close()
