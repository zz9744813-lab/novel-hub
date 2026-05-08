from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.config import DAILY_GOAL_WORDS
from app.deps import get_templates
from app.services.library_service import list_chapters, scan_projects
from app.services.metrics_service import compute_trend

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    projects = scan_projects()
    chapters = []
    for p in projects:
        for c in list_chapters(p["name"]):
            chapters.append({"project": p["name"], **c})
    chapters.sort(key=lambda x: x["modified"], reverse=True)

    # Calculate today's words from operation logs
    trend_data = compute_trend()
    today_words = trend_data[-1]["words"] if trend_data else 0

    # Better logic for quick project: the project with the most recently modified chapter
    quick_project = (
        chapters[0]["project"]
        if chapters
        else (projects[0]["name"] if projects else None)
    )

    templates = get_templates()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "projects": projects,
            "today_words": today_words,
            "total_words": sum(p["total_words"] for p in projects),
            "recent_chapters": chapters[:8],
            "trend": trend_data,
            "daily_goal": DAILY_GOAL_WORDS,
            "quick_project": quick_project,
        },
    )
