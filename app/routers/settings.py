from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.config import BACKUP_ROOT, DB_PATH, FEATURES, NOVELS_ROOT, VAULT_ROOT
from app.db import (
    clear_setting,
    get_conn,
    get_setting,
    get_setting_decrypted,
    set_setting,
    set_setting_encrypted,
)
from app.deps import get_templates
from app.security import require_auth
from app.services.library_service import list_chapters
from app.services.metrics_service import log_operation
from app.services.prompts_service import get_global_prompt

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> Response:
    if not request.session.get("authed"):
        return RedirectResponse("/login", status_code=303)
    with get_conn() as conn:
        log_count = conn.execute("SELECT COUNT(1) as c FROM operation_logs").fetchone()[
            "c"
        ]

    ai_api_key_configured = bool(get_setting_decrypted("ai_api_key", ""))
    ai_base_url = get_setting("ai_base_url", "https://api.openai.com/v1")
    ai_model = get_setting("ai_model", "gpt-3.5-turbo")
    global_prompt = get_global_prompt(get_setting)

    templates = get_templates()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "vault_root": str(VAULT_ROOT),
            "db_path": str(DB_PATH),
            "backup_root": str(BACKUP_ROOT),
            "login_state": "已登录",
            "log_count": log_count,
            "ai_api_key_configured": ai_api_key_configured,
            "ai_base_url": ai_base_url,
            "ai_model": ai_model,
            "features": FEATURES,
            "global_prompt": global_prompt,
        },
    )


@router.post("/settings/ai")
def update_ai_settings(
    request: Request,
    ai_api_key: str = Form(""),
    ai_base_url: str = Form(""),
    ai_model: str = Form(""),
    clear_ai_api_key: str = Form(""),
) -> Response:
    require_auth(request)
    if clear_ai_api_key == "1":
        clear_setting("ai_api_key")
    elif ai_api_key.strip():
        set_setting_encrypted("ai_api_key", ai_api_key.strip())
    set_setting("ai_base_url", ai_base_url)
    set_setting("ai_model", ai_model)
    log_operation("update_ai_settings")
    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/reindex")
def reindex_all(request: Request) -> Response:
    require_auth(request)
    if NOVELS_ROOT.exists():
        for p in NOVELS_ROOT.iterdir():
            if p.is_dir():
                list_chapters(p.name, sync=True)
    log_operation("reindex_all")
    return RedirectResponse(url="/settings", status_code=303)
