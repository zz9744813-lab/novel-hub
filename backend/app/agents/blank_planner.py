"""Streaming AI planner for a blank book; never writes database entities."""
from __future__ import annotations

import json
import uuid

from app.agents.caller import call_agent
from app.services.blank_planning import normalize_planning_draft

SYSTEM_PROMPT = """你是空白小说的企划规划 Agent。根据用户 premise 生成可审阅的 JSON 企划草案。
必须输出 title、logline、synopsis、genre、tone、themes、chapters。
chapters 必须恰好包含 target_chapter_count 个章节，chapter_no 从 1 连续到目标值。
每章必须有 title、goal、required_beats、forbidden_outcomes、depends_on、source_refs。
不要写正文，不要解释，不要 Markdown，不要编造外部资料；source_refs 没有来源时输出空数组。"""


async def generate_planning_draft(
    *,
    book_id: uuid.UUID,
    premise: str,
    genre: str,
    tone: str,
    themes: list[str],
    target_chapter_count: int,
) -> dict:
    payload = {
        "book_id": str(book_id),
        "premise": premise,
        "genre": genre,
        "tone": tone,
        "themes": themes,
        "target_chapter_count": target_chapter_count,
    }
    _run, publishable, meta = await call_agent(
        book_id=book_id,
        agent_role="blank_planner",
        user_content=json.dumps(payload, ensure_ascii=False),
        assembly_manifest={"entries": [], "source": "blank_planning"},
    )
    if publishable is None:
        raise ValueError(
            (meta or {}).get("block_reason")
            or (meta or {}).get("error")
            or "empty planning output"
        )
    if not isinstance(publishable, dict):
        raise ValueError("planning output must be a JSON object")
    return normalize_planning_draft(
        publishable,
        target_chapter_count=target_chapter_count,
    )
