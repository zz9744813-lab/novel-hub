from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.config import VAULT_ROOT
from app.db import get_conn
from app.services.markdown_service import (
    _ensure_under_root,
    _project_from_path,
    count_words,
    dump_frontmatter,
    utc_now,
    write_atomic,
)
from app.services.snapshot_service import backup_file
from app.services.wiki_link import update_entity_refs


def _infer_kind(p: Path) -> str:
    parts = p.parts
    if "chapters" in parts:
        return "chapter"
    if "hooks" in parts:
        return "hook"
    if "characters" in parts:
        return "character"
    if "world" in parts:
        return "world"
    return "other"


def normalize_meta(fm: dict[str, Any], stem: str, project: str = None) -> dict[str, Any]:
    def resolve_entities(items, kind):
        if not items:
            return []
        if not project:
            return items
        resolved = []
        with get_conn() as conn:
            for item in items:
                if isinstance(item, str):
                    # Try to bind ID by name
                    row = conn.execute(
                        "SELECT id FROM entities WHERE project=? AND name=?", (project, item)
                    ).fetchone()
                    if row:
                        resolved.append({"id": row["id"], "name": item})
                    else:
                        resolved.append({"id": None, "name": item})  # Unbound
                else:
                    resolved.append(item)
        return resolved

    res = {
        "title": fm.get("title", stem),
        "chapter": fm.get("chapter", ""),
        "status": fm.get("status", "draft"),
        "volume": fm.get("volume", ""),
        "tags": fm.get("tags", []),
        "synopsis": fm.get("synopsis", ""),
        "notes": fm.get("notes", ""),
        "pov": fm.get("pov", ""),
        "characters": resolve_entities(fm.get("characters", []), "character"),
        "locations": resolve_entities(fm.get("locations", []), "location"),
        "warnings": fm.get("warnings", []),
        "draft_version": fm.get("draft_version", ""),
    }
    return res


def _chapter_int(value: Any) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _prepare_frontmatter(
    frontmatter: dict[str, Any], path: Path, project: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    frontmatter = dict(frontmatter)
    meta = normalize_meta(frontmatter, path.stem, project=project)
    frontmatter["characters"] = meta["characters"]
    frontmatter["locations"] = meta["locations"]
    return frontmatter, meta


def _write_chapter_file(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    if path.exists():
        backup_file(path)
    content = dump_frontmatter(frontmatter, body)
    write_atomic(path, content)


def _upsert_file_index(
    conn: sqlite3.Connection,
    path: Path,
    project: str,
    meta: dict[str, Any],
    words: int,
    mtime: float,
) -> None:
    conn.execute(
        """
        INSERT INTO file_index(path, project, word_count, updated_at, title, chapter, chapter_int, status, volume, mtime, synopsis)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            project=excluded.project,
            word_count=excluded.word_count,
            updated_at=excluded.updated_at,
            title=excluded.title,
            chapter=excluded.chapter,
            chapter_int=excluded.chapter_int,
            status=excluded.status,
            volume=excluded.volume,
            mtime=excluded.mtime,
            synopsis=excluded.synopsis
        """,
        (
            str(path),
            project,
            words,
            utc_now().isoformat(),
            meta["title"],
            meta["chapter"],
            _chapter_int(meta["chapter"]),
            meta["status"],
            meta["volume"],
            mtime,
            meta.get("synopsis", ""),
        ),
    )


def _index_chapter_fts(
    conn: sqlite3.Connection, path: Path, project: str, meta: dict[str, Any], body: str
) -> None:
    conn.execute("DELETE FROM chapter_fts WHERE path=?", (str(path),))
    conn.execute(
        "INSERT INTO chapter_fts(path, project, kind, title, body) VALUES (?, ?, ?, ?, ?)",
        (str(path), project, _infer_kind(path), meta["title"], body),
    )


def _index_scenes(
    conn: sqlite3.Connection,
    path: Path,
    project: str,
    meta: dict[str, Any],
    body: str,
    words: int,
) -> None:
    conn.execute("DELETE FROM scenes WHERE chapter_path=?", (str(path),))
    scene_matches = list(re.finditer(r"^##\s+(.*)$", body, re.MULTILINE))
    if not scene_matches:
        # Whole chapter as one scene
        sc_id = f"sc_{hashlib.sha1((str(path) + '1').encode()).hexdigest()[:8]}"
        conn.execute(
            "INSERT INTO scenes(id, chapter_path, project, seq, title, word_count, char_offset_start, char_offset_end) VALUES (?,?,?,?,?,?,?,?)",
            (sc_id, str(path), project, 1, meta["title"], words, 0, len(body)),
        )
    else:
        for i, match in enumerate(scene_matches):
            title = match.group(1).strip()
            start = match.end()
            end = (
                scene_matches[i + 1].start()
                if i + 1 < len(scene_matches)
                else len(body)
            )
            scene_body = body[start:end]
            sc_id = f"sc_{hashlib.sha1((str(path) + str(i + 1)).encode()).hexdigest()[:8]}"
            conn.execute(
                "INSERT INTO scenes(id, chapter_path, project, seq, title, word_count, char_offset_start, char_offset_end) VALUES (?,?,?,?,?,?,?,?)",
                (
                    sc_id,
                    str(path),
                    project,
                    i + 1,
                    title,
                    count_words(scene_body),
                    start,
                    end,
                ),
            )


def write_markdown(
    path: Path, frontmatter: dict[str, Any], body: str, project: str = None
) -> None:
    p = _ensure_under_root(path, VAULT_ROOT)
    p.parent.mkdir(parents=True, exist_ok=True)

    if project is None:
        project = _project_from_path(p)

    frontmatter, meta = _prepare_frontmatter(frontmatter, p, project)
    _write_chapter_file(p, frontmatter, body)

    words = count_words(body)
    mtime = p.stat().st_mtime

    with get_conn() as conn:
        _upsert_file_index(conn, p, project, meta, words, mtime)
        _index_chapter_fts(conn, p, project, meta, body)
        _index_scenes(conn, p, project, meta, body, words)

    # Wiki Link Refs
    update_entity_refs(str(p), body, project)
