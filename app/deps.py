from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates

_templates: Jinja2Templates | None = None
_limiter: Any | None = None


def configure_runtime(*, templates_obj: Jinja2Templates, limiter_obj: Any = None) -> None:
    global _templates, _limiter
    _templates = templates_obj
    _limiter = limiter_obj


def get_templates() -> Jinja2Templates:
    if _templates is None:
        raise RuntimeError("templates not configured")
    return _templates


def get_limiter() -> Any:
    return _limiter
