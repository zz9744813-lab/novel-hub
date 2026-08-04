"""Shared task contract for the global task center.

The service deliberately keeps task-specific state machines in their owning
routers/workers. It only normalizes identity, presentation, filtering, and
pagination for the global task view.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from math import ceil
from typing import Any
from uuid import UUID

TASK_TYPES = ("chapter", "import", "research")


def normalize_task_type(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    value = value.strip().lower()
    aliases = {"chapter_run": "chapter", "import_session": "import", "research_session": "research"}
    value = aliases.get(value, value)
    if value not in TASK_TYPES:
        raise ValueError(f"unsupported task type: {value}")
    return value


def build_task_id(task_type: str, entity_id: UUID | str) -> str:
    normalized = normalize_task_type(task_type)
    if normalized is None:
        raise ValueError("task type is required")
    return f"{normalized}:{entity_id}"


def parse_task_id(value: str) -> tuple[str, UUID]:
    if not isinstance(value, str) or value.count(":") != 1:
        raise ValueError("task id must be '<type>:<uuid>'")
    raw_type, raw_id = value.split(":", 1)
    task_type = normalize_task_type(raw_type)
    if task_type is None or not raw_id:
        raise ValueError("task id must be '<type>:<uuid>'")
    try:
        return task_type, UUID(raw_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("task id contains an invalid UUID") from exc


def task_actions(task_type: str, status: str) -> list[str]:
    task_type = normalize_task_type(task_type) or ""
    status = (status or "").lower()
    if task_type == "chapter":
        if status in {"queued", "running", "drafting", "planning", "waiting_dependency"}:
            return ["pause", "cancel"]
        if status == "retryable":
            return ["pause", "cancel", "retry"]
        if status in {"paused", "needs_human", "resource_blocked", "blocked_by_dependency"}:
            return ["resume", "cancel"]
        if status == "failed":
            return ["retry"]
    elif task_type == "import":
        if status in {"uploaded", "queued", "analyzing", "preview_ready", "committing"}:
            return ["cancel", "retry"]
        if status in {"failed", "cancelled"}:
            return ["retry"]
    elif task_type == "research":
        if status in {"queued", "planning", "searching", "synthesizing", "running"}:
            return ["cancel"]
    return []


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _error(code: str | None, detail: Any) -> dict[str, Any] | None:
    if not code and not detail:
        return None
    return {"code": code, "detail": detail}


def serialize_chapter_run(run: Any, *, book_title: str | None = None) -> dict[str, Any]:
    item = {
        "task_id": build_task_id("chapter", run.id),
        "task_type": "chapter",
        "entity_id": str(run.id),
        "book_id": str(run.book_id) if run.book_id else None,
        "book_title": book_title,
        "chapter_id": str(run.chapter_id) if run.chapter_id else None,
        "chapter_no": run.chapter_no,
        "status": run.status,
        "progress": None,
        "current_step": run.current_step,
        "control_requested": run.control_requested,
        "error": _error(run.error_code, run.error_detail),
        "created_at": _iso(run.created_at),
        "updated_at": _iso(getattr(run, "updated_at", None) or run.created_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
    }
    item["actions"] = task_actions("chapter", run.status)
    return item


def serialize_import_session(session: Any, *, book_title: str | None = None) -> dict[str, Any]:
    item = {
        "task_id": build_task_id("import", session.id),
        "task_type": "import",
        "entity_id": str(session.id),
        "book_id": str(session.book_id) if session.book_id else None,
        "book_title": book_title,
        "chapter_id": None,
        "chapter_no": None,
        "status": session.status,
        "progress": session.progress,
        "current_step": session.current_step,
        "control_requested": getattr(session, "control_requested", None),
        "error": _error(getattr(session, "error_code", None), getattr(session, "error_detail", None)),
        "created_at": _iso(getattr(session, "created_at", None)),
        "updated_at": _iso(getattr(session, "updated_at", None) or getattr(session, "created_at", None)),
        "started_at": None,
        "finished_at": _iso(getattr(session, "completed_at", None)),
    }
    item["actions"] = task_actions("import", session.status)
    return item


def serialize_research_session(session: Any, *, book_title: str | None = None) -> dict[str, Any]:
    item = {
        "task_id": build_task_id("research", session.id),
        "task_type": "research",
        "entity_id": str(session.id),
        "book_id": str(session.book_id) if session.book_id else None,
        "book_title": book_title,
        "chapter_id": str(session.chapter_id) if getattr(session, "chapter_id", None) else None,
        "chapter_no": None,
        "status": session.status,
        "progress": None,
        "current_step": getattr(session, "trigger_type", None),
        "control_requested": None,
        "error": None,
        "created_at": _iso(getattr(session, "created_at", None)),
        "updated_at": _iso(getattr(session, "updated_at", None) or getattr(session, "created_at", None)),
        "started_at": None,
        "finished_at": _iso(getattr(session, "completed_at", None)),
        "topic": getattr(session, "requested_topic", None),
    }
    item["actions"] = task_actions("research", session.status)
    return item


def paginate_tasks(rows: list[dict[str, Any]], page: int = 1, page_size: int = 50) -> dict[str, Any]:
    page = max(1, int(page))
    page_size = min(100, max(1, int(page_size)))
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "items": rows[start : start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": ceil(total / page_size) if total else 0,
    }


def filter_and_paginate_tasks(
    rows: Iterable[dict[str, Any]],
    *,
    task_type: str | None = None,
    status: str | None = None,
    book_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    task_type = normalize_task_type(task_type)
    filtered = [
        row for row in rows
        if (task_type is None or row["task_type"] == task_type)
        and (status is None or row["status"] == status)
        and (book_id is None or row.get("book_id") == book_id)
    ]
    filtered.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
    return paginate_tasks(filtered, page, page_size)
