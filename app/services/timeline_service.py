from __future__ import annotations

from typing import Any
from pathlib import Path
from app.db import get_conn

def get_project_timeline(project: str, limit: int = 200) -> list[dict[str, Any]]:
    # We join with file_index to get the chapter_path using chapter_int
    sql = """
    SELECT
        h.id,
        h.entity_id,
        e.name AS entity_name,
        e.kind AS entity_kind,
        h.field,
        h.old_value,
        h.new_value,
        fi.path AS chapter_path,
        h.changed_at AS created_at
    FROM entity_history h
    JOIN entities e ON e.id = h.entity_id
    LEFT JOIN file_index fi ON fi.project = h.project AND fi.chapter_int = h.chapter_int
    WHERE e.project = ?
    ORDER BY h.changed_at DESC, h.id DESC
    LIMIT ?
    """

    items = []
    with get_conn() as conn:
        for row in conn.execute(sql, (project, limit)).fetchall():
            item = dict(row)

            # truncate old_value / new_value to 80 chars
            old_val = item["old_value"] or ""
            new_val = item["new_value"] or ""
            if len(old_val) > 80:
                old_val = old_val[:80] + "..."
            if len(new_val) > 80:
                new_val = new_val[:80] + "..."

            item["old_value"] = old_val
            item["new_value"] = new_val

            # summary generation
            if item["field"] == "name":
                item["summary"] = f"{old_val} → {new_val}"
            else:
                item["summary"] = f"{item['field']}: {old_val} → {new_val}"

            # chapter_filename
            cpath = item.get("chapter_path")
            item["chapter_filename"] = Path(cpath).name if cpath else ""

            # empty created_at fallback
            if not item.get("created_at"):
                item["created_at"] = ""

            items.append(item)

    return items
