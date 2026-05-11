from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import NOVELS_ROOT
from app.services.markdown_service import dump_frontmatter, read_markdown, safe_slug, write_atomic
from app.services.metrics_service import log_operation
from app.services.path_service import project_path

THREAD_STATUSES = {
    "open": "待铺垫",
    "active": "推进中",
    "resolved": "已回收",
    "dropped": "已废弃",
}

def list_threads(project: str) -> list[dict[str, Any]]:
    safe_project = safe_slug(project, fallback="project")
    hooks_dir = project_path(safe_project) / "hooks"

    if not hooks_dir.exists():
        return []

    threads = []
    for p in hooks_dir.glob("*.md"):
        fm, body = read_markdown(p)
        filename = p.name
        slug = p.stem
        title = fm.get("title") or slug

        status = fm.get("status") or "open"
        if status not in THREAD_STATUSES:
            status = "open"

        priority = fm.get("priority") or "normal"
        chapter = fm.get("chapter") or ""
        payoff_chapter = fm.get("payoff_chapter") or ""

        raw_tags = fm.get("tags")
        if isinstance(raw_tags, str):
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        elif isinstance(raw_tags, list):
            tags = raw_tags
        else:
            tags = []

        excerpt = body.strip()[:160]
        updated_at = datetime.fromtimestamp(p.stat().st_mtime).isoformat()

        threads.append({
            "filename": filename,
            "slug": slug,
            "title": title,
            "status": status,
            "priority": priority,
            "chapter": chapter,
            "payoff_chapter": payoff_chapter,
            "tags": tags,
            "excerpt": excerpt,
            "body": body.strip(),
            "updated_at": updated_at,
        })

    status_order = {"open": 0, "active": 1, "resolved": 2, "dropped": 3}
    priority_order = {"high": 0, "normal": 1, "low": 2}

    threads.sort(key=lambda t: (
        status_order.get(t["status"], 0),
        priority_order.get(t["priority"], 1),
        t["updated_at"]
    ), reverse=True)

    return threads

def get_threads_board(project: str) -> dict[str, Any]:
    threads = list_threads(project)

    columns = {
        "open": [],
        "active": [],
        "resolved": [],
        "dropped": []
    }

    for t in threads:
        columns[t["status"]].append(t)

    return {
        "statuses": THREAD_STATUSES,
        "columns": columns,
        "total": len(threads)
    }

def create_thread(project: str, title: str, body: str = "", status: str = "open", priority: str = "normal") -> dict[str, Any]:
    safe_project = safe_slug(project, fallback="project")
    hooks_dir = project_path(safe_project) / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    base_slug = safe_slug(title, fallback="thread")
    filename = f"{base_slug}.md"
    path = hooks_dir / filename

    counter = 2
    while path.exists():
        filename = f"{base_slug}-{counter}.md"
        path = hooks_dir / filename
        counter += 1

    if status not in THREAD_STATUSES:
        status = "open"

    fm = {
        "title": title,
        "status": status,
        "priority": priority,
        "chapter": "",
        "payoff_chapter": "",
        "tags": []
    }

    content = dump_frontmatter(fm, body)
    write_atomic(path, content)

    log_operation("create_thread", str(path), project=safe_project)

    threads = list_threads(project)
    for t in threads:
        if t["filename"] == filename:
            return t
    return {}

def update_thread(project: str, filename: str, data: dict[str, Any]) -> dict[str, Any]:
    safe_project = safe_slug(project, fallback="project")
    hooks_dir = project_path(safe_project) / "hooks"
    path = hooks_dir / filename

    if not path.exists():
        raise FileNotFoundError(f"Thread file {filename} not found.")

    fm, body = read_markdown(path)

    if "status" in data and data["status"] not in THREAD_STATUSES:
        raise ValueError("Invalid status")

    if "title" in data:
        fm["title"] = data["title"]
    if "status" in data:
        fm["status"] = data["status"]
    if "priority" in data:
        fm["priority"] = data["priority"]
    if "chapter" in data:
        fm["chapter"] = data["chapter"]
    if "payoff_chapter" in data:
        fm["payoff_chapter"] = data["payoff_chapter"]
    if "tags" in data:
        raw_tags = data["tags"]
        if isinstance(raw_tags, str):
            fm["tags"] = [t.strip() for t in raw_tags.split(",") if t.strip()]
        else:
            fm["tags"] = raw_tags

    if "body" in data:
        body = data["body"]

    content = dump_frontmatter(fm, body)
    write_atomic(path, content)

    log_operation("update_thread", str(path), project=safe_project)

    threads = list_threads(project)
    for t in threads:
        if t["filename"] == filename:
            return t
    return {}

def delete_thread(project: str, filename: str) -> None:
    safe_project = safe_slug(project, fallback="project")
    hooks_dir = project_path(safe_project) / "hooks"
    path = hooks_dir / filename

    if path.exists():
        path.unlink()
        log_operation("delete_thread", str(path), project=safe_project)
    else:
        raise FileNotFoundError(f"Thread file {filename} not found.")
