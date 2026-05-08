from datetime import timedelta
from typing import Any
from app.config import PROJECT_GOAL_WORDS, DAILY_GOAL_WORDS
from app.db import get_conn
from app.services.markdown_service import utc_now


def get_project_meta(project: str) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM project_meta WHERE project=?", (project,)).fetchone()
        if row:
            return dict(row)
        return {"project": project, "target_words": PROJECT_GOAL_WORDS, "author": "", "synopsis": "", "daily_goal": DAILY_GOAL_WORDS}


def set_project_meta(project: str, target_words: int = 100000, author: str = "", synopsis: str = "", daily_goal: int = 2000) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO project_meta(project, target_words, author, synopsis, daily_goal)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(project) DO UPDATE SET
                   target_words=excluded.target_words,
                   author=excluded.author,
                   synopsis=excluded.synopsis,
                   daily_goal=excluded.daily_goal""",
            (project, target_words, author, synopsis, daily_goal)
        )


def compute_trend() -> list[dict[str, Any]]:
    trend = []
    now = utc_now()

    with get_conn() as conn:
        for i in range(6, -1, -1):
            day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)

            rows = conn.execute(
                "SELECT detail FROM operation_logs WHERE action = 'save' AND created_at >= ? AND created_at < ?",
                (day_start.isoformat(), day_end.isoformat())
            ).fetchall()

            words_added = 0
            for r in rows:
                try:
                    words_added += int(r["detail"].replace("words_added=", ""))
                except:
                    pass

            trend.append({
                "day": day_start.strftime("%m-%d"),
                "words": words_added
            })
    return trend
