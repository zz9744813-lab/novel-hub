from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db import get_conn


def get_entity_arc(ent_id: str) -> dict[str, Any]:
    with get_conn() as conn:
        entity_row = conn.execute(
            "SELECT id, project, kind, name FROM entities WHERE id=?",
            (ent_id,)
        ).fetchone()

        if not entity_row:
            raise ValueError(f"Entity not found: {ent_id}")

        entity = dict(entity_row)

        events_rows = conn.execute(
            """
            SELECT
                h.id, h.field, h.old_value, h.new_value, h.chapter_int, h.changed_at,
                fi.path AS chapter_path, fi.title AS chapter_title
            FROM entity_history h
            LEFT JOIN file_index fi ON fi.project = h.project AND fi.chapter_int = h.chapter_int
            WHERE h.entity_id = ?
            ORDER BY COALESCE(h.chapter_int, 999999), h.changed_at, h.id
            """,
            (ent_id,)
        ).fetchall()

    events = []
    field_groups = {}

    first_changed_at = None
    last_changed_at = None

    for row in events_rows:
        event = dict(row)

        old_val = str(event["old_value"]) if event["old_value"] is not None else ""
        new_val = str(event["new_value"]) if event["new_value"] is not None else ""

        # truncate
        if len(old_val) > 120:
            old_val = old_val[:120] + "..."
        if len(new_val) > 120:
            new_val = new_val[:120] + "..."

        event["old_value"] = old_val
        event["new_value"] = new_val

        if event["field"] == "name":
            event["summary"] = f"{old_val} → {new_val}"
        else:
            event["summary"] = f"{event['field']}: {old_val} → {new_val}"

        chap_path = event.get("chapter_path")
        event["chapter_filename"] = Path(chap_path).name if chap_path else ""

        events.append(event)

        field = event["field"]
        if field not in field_groups:
            field_groups[field] = []
        field_groups[field].append(event)

        changed_at = event.get("changed_at")
        if changed_at:
            if first_changed_at is None or changed_at < first_changed_at:
                first_changed_at = changed_at
            if last_changed_at is None or changed_at > last_changed_at:
                last_changed_at = changed_at

    stats = {
        "event_count": len(events),
        "field_count": len(field_groups),
        "first_changed_at": first_changed_at,
        "last_changed_at": last_changed_at,
    }

    return {
        "entity": entity,
        "events": events,
        "field_groups": field_groups,
        "stats": stats,
    }
