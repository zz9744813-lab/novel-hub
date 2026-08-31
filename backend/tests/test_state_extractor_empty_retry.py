from __future__ import annotations

import hashlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.state_extractor import extract_candidates


def _outline() -> dict:
    return {
        "chapter_no": 1,
        "goal": "完成本章行动",
        "expected_state_changes": [
            {"kind": "turn", "description": "明确改变位置"},
        ],
        "involved_character_ids": ["char-1"],
    }


def _explicit_event() -> dict:
    content = "正文明确写出人物走入屋内。"
    return {
        "event_key": "evt-1",
        "entity_type": "character",
        "entity_id": "char-1",
        "field": "location",
        "old_value": "门外",
        "new_value": "屋内",
        "certainty": "explicit",
        "scene_no": 1,
        "evidence_paragraph_key": "p-1",
        "evidence_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "evidence": content,
    }


def _scenes() -> list[dict]:
    return [
        {
            "scene_no": 1,
            "content": "正文明确写出人物走入屋内。",
            "paragraphs": [
                {
                    "paragraph_key": "p-1",
                    "content": "正文明确写出人物走入屋内。",
                }
            ],
        }
    ]


@pytest.mark.asyncio
async def test_expected_empty_extract_gets_one_bounded_repair_call():
    caller = AsyncMock(
        side_effect=[
            (object(), {"events": [], "conflicts": []}, {"block_reason": "empty_final_content"}),
            (object(), {"events": [_explicit_event()]}, {}),
        ]
    )

    with patch("app.agents.state_extractor.call_agent", caller):
        ok, events, errors, extras = await extract_candidates(
            book_id=uuid.uuid4(),
            chapter_id=uuid.uuid4(),
            chapter_no=1,
            chapter_content="正文明确写出人物走入屋内。" * 100,
            scenes=_scenes(),
            outline_node=_outline(),
            current_l4={},
        )

    assert ok is True
    assert [event["event_key"] for event in events] == ["evt-1"]
    assert errors == []
    assert extras == {"reaction_evidence": [], "attributions": []}
    assert caller.await_count == 2
    retry_prompt = caller.await_args_list[1].kwargs["user_content"]
    assert "EMPTY_EXTRACT_RETRY" in retry_prompt
    assert "不得根据大纲或推测补写事实" in retry_prompt


@pytest.mark.asyncio
async def test_expected_empty_extract_still_fails_closed_after_retry():
    empty = {"events": [], "conflicts": []}
    caller = AsyncMock(side_effect=[(object(), empty, {}), (object(), empty, {})])

    with patch("app.agents.state_extractor.call_agent", caller):
        ok, events, errors, _extras = await extract_candidates(
            book_id=uuid.uuid4(),
            chapter_id=uuid.uuid4(),
            chapter_no=1,
            chapter_content="正文。" * 100,
            scenes=_scenes(),
            outline_node=_outline(),
            current_l4={},
        )

    assert ok is False
    assert events == []
    assert errors == ["expected_state_changes_but_empty_extract"]
    assert caller.await_count == 2


@pytest.mark.asyncio
async def test_malformed_explicit_event_is_filtered_before_early_stop():
    malformed = {
        "event_key": "bad-event",
        "certainty": "explicit",
        "scene_no": "not-an-integer",
    }
    caller = AsyncMock(
        side_effect=[
            (object(), {"events": [malformed]}, {}),
            (object(), {"events": [_explicit_event()]}, {}),
        ]
    )

    with patch("app.agents.state_extractor.call_agent", caller):
        ok, events, errors, _extras = await extract_candidates(
            book_id=uuid.uuid4(),
            chapter_id=uuid.uuid4(),
            chapter_no=1,
            chapter_content="正文明确写出人物走入屋内。" * 100,
            scenes=_scenes(),
            outline_node=_outline(),
            current_l4={},
        )

    assert ok is True
    assert [event["event_key"] for event in events] == ["evt-1"]
    assert errors == []
    assert caller.await_count == 2


@pytest.mark.asyncio
async def test_legal_explicit_event_stops_without_a_second_call():
    caller = AsyncMock(
        return_value=(object(), {"events": [_explicit_event()]}, {})
    )

    with patch("app.agents.state_extractor.call_agent", caller):
        ok, events, errors, _extras = await extract_candidates(
            book_id=uuid.uuid4(),
            chapter_id=uuid.uuid4(),
            chapter_no=1,
            chapter_content="正文明确写出人物走入屋内。" * 100,
            scenes=_scenes(),
            outline_node=_outline(),
            current_l4={},
        )

    assert ok is True
    assert [event["event_key"] for event in events] == ["evt-1"]
    assert errors == []
    assert caller.await_count == 1


@pytest.mark.asyncio
async def test_no_expected_changes_does_not_spend_a_retry_call():
    caller = AsyncMock(return_value=(object(), {"events": []}, {}))
    outline = {**_outline(), "expected_state_changes": [], "involved_character_ids": []}

    with patch("app.agents.state_extractor.call_agent", caller):
        ok, events, errors, _extras = await extract_candidates(
            book_id=uuid.uuid4(),
            chapter_id=uuid.uuid4(),
            chapter_no=1,
            chapter_content="",
            scenes=[],
            outline_node=outline,
            current_l4={},
        )

    assert (ok, events, errors) == (True, [], [])
    caller.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ungrounded",
    [
        {
            **_explicit_event(),
            "evidence_paragraph_key": None,
            "evidence_hash": None,
            "evidence": None,
        },
        {
            **_explicit_event(),
            "evidence_paragraph_key": "missing-paragraph",
        },
        {
            **_explicit_event(),
            "evidence_hash": "0" * 64,
        },
    ],
    ids=["no-evidence", "unknown-key", "wrong-hash"],
)
async def test_schema_valid_but_ungrounded_event_gets_repair_call(ungrounded):
    caller = AsyncMock(
        side_effect=[
            (object(), {"events": [ungrounded]}, {}),
            (object(), {"events": [_explicit_event()]}, {}),
        ]
    )

    with patch("app.agents.state_extractor.call_agent", caller):
        ok, events, errors, _extras = await extract_candidates(
            book_id=uuid.uuid4(),
            chapter_id=uuid.uuid4(),
            chapter_no=1,
            chapter_content="正文明确写出人物走入屋内。" * 100,
            scenes=_scenes(),
            outline_node=_outline(),
            current_l4={},
        )

    assert ok is True
    assert [event["event_key"] for event in events] == ["evt-1"]
    assert errors == []
    assert caller.await_count == 2


@pytest.mark.asyncio
async def test_excerpt_without_key_is_grounded_in_body_and_stops_once():
    event = {
        **_explicit_event(),
        "evidence_paragraph_key": None,
        "evidence_hash": None,
    }
    caller = AsyncMock(return_value=(object(), {"events": [event]}, {}))

    with patch("app.agents.state_extractor.call_agent", caller):
        ok, events, errors, _extras = await extract_candidates(
            book_id=uuid.uuid4(),
            chapter_id=uuid.uuid4(),
            chapter_no=1,
            chapter_content="正文明确写出人物走入屋内。" * 100,
            scenes=_scenes(),
            outline_node=_outline(),
            current_l4={},
        )

    assert ok is True
    assert [event["event_key"] for event in events] == ["evt-1"]
    assert errors == []
    assert caller.await_count == 1
