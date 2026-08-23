"""Structured failure diagnostics (spec §17).

Failures must never surface as a bare code like "START_URL_FETCH_FAILED". Every
failure is normalized into a structured dict the UI renders as 阶段/原因/建议.
"""
from __future__ import annotations

from typing import Any

STAGE_LABELS = {
    "fetch": "抓取",
    "discover": "目录解析",
    "parse": "正文解析",
    "persist": "落库",
    "export": "导出",
    "import": "导入",
}


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage)


def build_diagnostic(
    *,
    stage: str,
    code: str,
    url: str | None = None,
    http_status: int | None = None,
    selector: str | None = None,
    selector_hits: int = 0,
    anti_bot: str | None = None,
    suggested_action: str | None = None,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "code": code,
        "url": url,
        "http_status": http_status,
        "selector": selector,
        "selector_hits": selector_hits,
        "anti_bot": anti_bot,
        "suggested_action": suggested_action,
    }


def from_probe_result(result: dict[str, Any]) -> dict[str, Any]:
    """Map a probe result into a structured diagnostic for display (spec §9, §17)."""
    if result.get("status") == "blocked":
        return build_diagnostic(
            stage="fetch",
            code="SOURCE_BLOCKED",
            url=result.get("final_url"),
            http_status=result.get("http_status"),
            anti_bot=result.get("anti_bot_type"),
            suggested_action="该地址存在访问控制，请改用合法可访问的页面",
        )
    if result.get("status") == "failed":
        return build_diagnostic(
            stage="parse",
            code="SELECTOR_NO_MATCH",
            url=result.get("final_url"),
            http_status=result.get("http_status"),
            selector_hits=result.get("content_hit_count", 0),
            suggested_action="先运行书源测试，或更新该来源的选择器规则",
        )
    return build_diagnostic(
        stage="parse",
        code="OK",
        url=result.get("final_url"),
        http_status=result.get("http_status"),
    )
