from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.db import get_conn
from app.services.markdown_service import utc_now


def log_operation(
    action: str, target: str = "", detail: str = "", value: int = 0, project: str = ""
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO operation_logs(action, target, project, created_at, detail, value) VALUES (?, ?, ?, ?, ?, ?)",
            (action, target, project, utc_now().isoformat(), detail, value),
        )


def compute_trend() -> list[dict[str, Any]]:
    trend = []
    now = utc_now()

    with get_conn() as conn:
        for i in range(6, -1, -1):
            day_start = (now - timedelta(days=i)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            day_end = day_start + timedelta(days=1)

            rows = conn.execute(
                "SELECT detail FROM operation_logs WHERE action = 'save' AND created_at >= ? AND created_at < ?",
                (day_start.isoformat(), day_end.isoformat()),
            ).fetchall()

            words_added = 0
            for r in rows:
                try:
                    words_added += int(r["detail"].replace("words_added=", ""))
                except:
                    pass

            trend.append({"day": day_start.strftime("%m-%d"), "words": words_added})
    return trend


def get_project_stats(project: str, days: int = 90) -> list[dict[str, Any]]:
    with get_conn() as conn:
        # Aggregate words_added from operation_logs
        rows = conn.execute(
            """
            SELECT date(created_at) as day, sum(value) as words
            FROM operation_logs
            WHERE action='save' AND project=?
            GROUP BY day
            ORDER BY day DESC
            LIMIT ?
            """,
            (project, days),
        ).fetchall()

    return [{"day": r["day"], "words": r["words"]} for r in rows]
