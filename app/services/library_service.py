from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DAILY_GOAL_WORDS, NOVELS_ROOT, PROJECT_GOAL_WORDS
from app.db import get_conn
from app.services.chapter_service import _infer_kind, normalize_meta
from app.services.markdown_service import count_words, read_markdown, utc_now
from app.services.path_service import list_markdown_files, project_path
from app.services.project_service import get_project_meta


def _reindex_notes_for_project(project: str) -> None:
    """Re-populate chapter_fts for character/world/hooks notes. Used after FTS schema migration."""
    proj_dir = NOVELS_ROOT / project
    if not proj_dir.exists():
        return
    with get_conn() as conn:
        for sub in ("characters", "world", "hooks"):
            sub_dir = proj_dir / sub
            if not sub_dir.exists():
                continue
            for f in sub_dir.glob("*.md"):
                try:
                    fm, body = read_markdown(f)
                    title = fm.get("title", f.stem) if fm else f.stem
                    conn.execute("DELETE FROM chapter_fts WHERE path=?", (str(f),))
                    conn.execute(
                        "INSERT INTO chapter_fts(path, project, kind, title, body) VALUES (?, ?, ?, ?, ?)",
                        (str(f), project, _infer_kind(f), title, body),
                    )
                except Exception:
                    pass


def list_chapters(project: str, sync: bool = False) -> list[dict[str, Any]]:
    folder = project_path(project) / "chapters"
    rows: list[dict[str, Any]] = []
    with get_conn() as conn:
        if sync:
            db_rows = conn.execute(
                "SELECT path, word_count, updated_at, title, chapter, chapter_int, status, volume, mtime "
                "FROM file_index WHERE project=? ORDER BY chapter_int, path",
                (project,),
            ).fetchall()
            cached_map = {r["path"]: dict(r) for r in db_rows}

            for f in list_markdown_files(folder):
                mtime = f.stat().st_mtime
                f_str = str(f)
                cached = cached_map.get(f_str)

                if not cached or cached.get("mtime") != mtime:
                    fm, body = read_markdown(f)
                    meta = normalize_meta(fm, f.stem, project=project)
                    words = count_words(body)
                    try:
                        chapter_int = int(meta["chapter"])
                    except (ValueError, TypeError):
                        chapter_int = 0

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
                            f_str,
                            project,
                            words,
                            utc_now().isoformat(),
                            meta["title"],
                            meta["chapter"],
                            chapter_int,
                            meta["status"],
                            meta["volume"],
                            mtime,
                            meta.get("synopsis", ""),
                        ),
                    )

                    conn.execute("DELETE FROM chapter_fts WHERE path=?", (f_str,))
                    conn.execute(
                        "INSERT INTO chapter_fts(path, project, kind, title, body) VALUES (?, ?, ?, ?, ?)",
                        (f_str, project, _infer_kind(f), meta["title"], body),
                    )

        db_rows = conn.execute(
            "SELECT * FROM file_index WHERE project=? ORDER BY chapter_int, path",
            (project,),
        ).fetchall()

        for r in db_rows:
            f = Path(r["path"])
            item = dict(r)
            item["filename"] = f.name
            item["modified"] = datetime.fromtimestamp(r["mtime"], tz=timezone.utc)
            rows.append(item)
    return rows


def list_notes(project: str, folder_name: str) -> list[dict[str, Any]]:
    folder = project_path(project) / folder_name
    rows = []
    for f in list_markdown_files(folder):
        _, body = read_markdown(f)
        excerpt = body.strip().replace("\n", " ")[:120]
        rows.append(
            {
                "name": f.stem,
                "filename": f.name,
                "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc),
                "word_count": count_words(body),
                "excerpt": excerpt,
            }
        )
    return rows


def scan_projects() -> list[dict[str, Any]]:
    if not NOVELS_ROOT.exists():
        return []
    results = []
    for p in sorted([item for item in NOVELS_ROOT.iterdir() if item.is_dir()]):
        project = p.name
        chapters = list_chapters(project)
        meta = get_project_meta(project)
        target = meta.get("target_words", PROJECT_GOAL_WORDS) or PROJECT_GOAL_WORDS
        total_words = sum(c["word_count"] for c in chapters)
        latest = max((c["modified"] for c in chapters), default=None)
        with get_conn() as conn:
            counts = conn.execute(
                """SELECT
                   COUNT(CASE WHEN kind='character' THEN 1 END) as character_count,
                   COUNT(CASE WHEN kind='location' THEN 1 END) as location_count,
                   COUNT(CASE WHEN kind='thread' THEN 1 END) as thread_count
                   FROM entities WHERE project=?""",
                (project,),
            ).fetchone()
            world_count = conn.execute(
                "SELECT COUNT(*) FROM entities WHERE project=? AND kind NOT IN ('character', 'thread')",
                (project,),
            ).fetchone()[0]

        results.append(
            {
                "name": project,
                "status": "active" if chapters else "planning",
                "chapter_count": len(chapters),
                "character_count": counts["character_count"],
                "location_count": counts["location_count"],
                "thread_count": counts["thread_count"],
                "world_count": world_count,
                "total_words": total_words,
                "target_words": target,
                "progress": min(100, int(total_words / target * 100)) if target else 0,
                "latest": latest,
                "author": meta.get("author", ""),
                "synopsis": meta.get("synopsis", ""),
                "daily_goal": meta.get("daily_goal", DAILY_GOAL_WORDS),
            }
        )
    return results
