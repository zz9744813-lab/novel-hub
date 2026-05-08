from typing import Any
from app.config import PROJECT_GOAL_WORDS, DAILY_GOAL_WORDS
from app.db import get_conn


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
