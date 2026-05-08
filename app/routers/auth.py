from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import app.config as config
from app.deps import get_templates
from app.services.metrics_service import log_operation


def create_router(limiter=None) -> APIRouter:
    router = APIRouter()

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> Response:
        templates = get_templates()
        return templates.TemplateResponse("login.html", {"request": request, "error": ""})

    def login(request: Request, password: str = Form(...)) -> Response:
        templates = get_templates()
        if not config.ADMIN_PASSWORD:
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "系统未初始化 (NOVELHUB_PASSWORD 未配置)，请联系管理员。",
                },
                status_code=200,
            )
        if password != config.ADMIN_PASSWORD:
            return templates.TemplateResponse(
                "login.html", {"request": request, "error": "密码错误"}, status_code=401
            )
        request.session["authed"] = True
        log_operation("login", detail="admin login")
        return RedirectResponse(url="/", status_code=303)

    if limiter is not None:
        login = limiter.limit("5/minute")(login)

    router.add_api_route(
        "/login", login, methods=["POST"], response_class=HTMLResponse
    )

    @router.get("/logout")
    def logout(request: Request) -> Response:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    return router
