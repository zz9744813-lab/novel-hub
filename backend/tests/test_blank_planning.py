from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.blank_planning import (
    PlanningValidationError,
    build_outline_nodes,
    normalize_planning_draft,
)


@pytest.mark.asyncio
async def test_planner_uses_unified_agent_caller_and_bound_role():
    from app.agents.blank_planner import generate_planning_draft

    draft = {
        "title": "x",
        "logline": "y",
        "synopsis": "z",
        "genre": "",
        "tone": "",
        "themes": [],
        "chapters": [{"chapter_no": 1, "title": "一", "goal": "开始"}],
    }
    with patch("app.agents.blank_planner.call_agent", new_callable=AsyncMock) as caller:
        caller.return_value = (None, draft, {"model_used": "locked-model"})
        result = await generate_planning_draft(
            book_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            premise="premise",
            genre="",
            tone="",
            themes=[],
            target_chapter_count=1,
        )

    assert result["title"] == "x"
    caller.assert_awaited_once()
    assert caller.await_args.kwargs["agent_role"] == "blank_planner"


def test_normalize_planning_draft_requires_core_metadata_and_chapters():
    draft = normalize_planning_draft(
        {
            "title": "雾城回响",
            "logline": "失忆的测绘师在雾城寻找被抹去的妹妹。",
            "synopsis": "她必须在城门关闭前查清真相。",
            "genre": "悬疑奇幻",
            "tone": "压抑、克制、渐进揭谜",
            "themes": ["记忆", "亲情"],
            "chapters": [
                {
                    "chapter_no": 1,
                    "title": "雾中来信",
                    "goal": "主角收到妹妹留下的异常地图。",
                    "required_beats": ["发现地图"],
                    "forbidden_outcomes": ["直接揭示真相"],
                }
            ],
        },
        target_chapter_count=1,
    )

    assert draft["title"] == "雾城回响"
    assert draft["chapters"][0]["chapter_no"] == 1
    assert draft["chapters"][0]["source_refs"] == []


def test_normalize_planning_draft_rejects_missing_or_duplicate_chapters():
    with pytest.raises(PlanningValidationError, match="logline"):
        normalize_planning_draft({"title": "x", "chapters": []}, target_chapter_count=1)

    with pytest.raises(PlanningValidationError, match="unique"):
        normalize_planning_draft(
            {
                "title": "x",
                "logline": "y",
                "synopsis": "z",
                "chapters": [
                    {"chapter_no": 1, "goal": "a"},
                    {"chapter_no": 1, "goal": "b"},
                ],
            },
            target_chapter_count=2,
        )


def test_build_outline_nodes_maps_dependencies_without_creating_entities_early():
    draft = normalize_planning_draft(
        {
            "title": "x",
            "logline": "y",
            "synopsis": "z",
            "chapters": [
                {"chapter_no": 1, "title": "一", "goal": "开始"},
                {"chapter_no": 2, "title": "二", "goal": "推进", "depends_on": [1]},
            ],
        },
        target_chapter_count=2,
    )

    nodes = build_outline_nodes(draft, book_id="book", outline_version_id="version")

    assert [node["chapter_no"] for node in nodes] == [1, 2]
    assert nodes[1]["depends_on"] == [{"node_id": 1}]
    assert all(node["source_refs"] == [] for node in nodes)
