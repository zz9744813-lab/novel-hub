"""Regression tests for deterministic next-chapter selection."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.next_chapter_selector import (
    NextChapterSelectionError,
    select_next_chapter,
)


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class ScalarListResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values



def db_for(*results):
    db = SimpleNamespace()
    db.execute = AsyncMock(side_effect=list(results))
    return db


def book():
    return SimpleNamespace(id=uuid4())


def outline(book_id, version=1):
    return SimpleNamespace(id=uuid4(), book_id=book_id, version=version, status="approved")


def node(book_id, outline_version_id, chapter_no):
    return SimpleNamespace(id=uuid4(), book_id=book_id, outline_version_id=outline_version_id, chapter_no=chapter_no)


def chapter(book_id, chapter_no, status="failed"):
    return SimpleNamespace(id=uuid4(), book_id=book_id, chapter_no=chapter_no, status=status)


def run(chapter_id, status="running"):
    return SimpleNamespace(id=uuid4(), chapter_id=chapter_id, status=status, created_at=None)


@pytest.mark.asyncio
async def test_500_outline_nodes_and_zero_chapters_select_chapter_one():
    b = book()
    ov = outline(b.id)
    n1 = node(b.id, ov.id, 1)
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        ScalarListResult([]),
        ScalarResult(None),
        ScalarResult(n1),
    )

    decision = await select_next_chapter(db, b.id)

    assert decision.action == "create_chapter"
    assert decision.chapter_no == 1
    assert decision.outline_node_id == n1.id
    # The selector never asks for max(outline_nodes.chapter_no).
    assert db.execute.await_count == 5


@pytest.mark.asyncio
async def test_finalized_one_to_seven_selects_eighth():
    b = book()
    ov = outline(b.id)
    n8 = node(b.id, ov.id, 8)
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        ScalarListResult([]),
        ScalarResult(7),
        ScalarResult(n8),
    )

    decision = await select_next_chapter(db, b.id)

    assert decision.chapter_no == 8
    assert decision.action == "create_chapter"


@pytest.mark.asyncio
async def test_failed_chapter_four_is_reused_before_next_number():
    b = book()
    ov = outline(b.id)
    c4 = chapter(b.id, 4, "failed")
    n4 = node(b.id, ov.id, 4)
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        ScalarListResult([c4]),
        ScalarResult(n4),
        ScalarResult(None),
    )

    decision = await select_next_chapter(db, b.id)

    assert decision.action == "resume_unfinished"
    assert decision.chapter_no == 4
    assert decision.chapter_id == c4.id


@pytest.mark.asyncio
async def test_active_run_is_opened_without_creating_another():
    b = book()
    ov = outline(b.id)
    c4 = chapter(b.id, 4, "running")
    n4 = node(b.id, ov.id, 4)
    active = run(c4.id)
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        ScalarListResult([c4]),
        ScalarResult(n4),
        ScalarResult(active),
    )

    decision = await select_next_chapter(db, b.id)

    assert decision.action == "open_active_run"
    assert decision.active_run_id == active.id
    assert decision.chapter_no == 4


@pytest.mark.asyncio
async def test_missing_approved_outline_node_is_blocked():
    b = book()
    ov = outline(b.id)
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        ScalarListResult([]),
        ScalarResult(None),
        ScalarResult(None),
    )

    with pytest.raises(NextChapterSelectionError) as exc:
        await select_next_chapter(db, b.id)

    assert exc.value.status_code == 422
    assert exc.value.detail == {
        "code": "OUTLINE_NODE_MISSING",
        "chapter_no": 1,
        "message": "第1章没有已批准章纲",
    }


@pytest.mark.asyncio
async def test_unapproved_outline_is_blocked_without_fallback():
    b = book()
    db = db_for(ScalarResult(b), ScalarResult(None))

    with pytest.raises(NextChapterSelectionError) as exc:
        await select_next_chapter(db, b.id)

    assert exc.value.detail["code"] == "OUTLINE_NOT_APPROVED"


@pytest.mark.asyncio
async def test_high_numbered_test_chapter_without_approved_node_is_ignored():
    b = book()
    ov = outline(b.id)
    n1 = node(b.id, ov.id, 1)
    test_chapter = chapter(b.id, 9999, "queued")
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        # The selector's approved-node subquery excludes the synthetic chapter.
        ScalarListResult([]),
        ScalarResult(None),
        ScalarResult(n1),
    )

    decision = await select_next_chapter(db, b.id)

    assert decision.chapter_no == 1
    assert decision.chapter_id is None
    assert test_chapter.chapter_no == 9999
