"""Regression tests for deterministic next-chapter selection.

Mock-level logic checks; real-database behaviour (identity binding, outline
rebinding, concurrency) is covered by test_session_recovery_db.py.
"""
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


class RowListResult:
    """Result of a column select: .all() returns (chapter_no, node_id) rows."""

    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


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
async def test_outline_nodes_and_zero_chapters_select_chapter_one():
    b = book()
    ov = outline(b.id)
    n1 = node(b.id, ov.id, 1)
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        RowListResult([(1, n1.id)]),
        ScalarListResult([]),
    )

    decision = await select_next_chapter(db, b.id)

    assert decision.action == "create_chapter"
    assert decision.chapter_no == 1
    assert decision.outline_node_id == n1.id
    assert decision.chapter_id is None
    assert db.execute.await_count == 4


@pytest.mark.asyncio
async def test_finalized_one_to_seven_selects_eighth():
    b = book()
    ov = outline(b.id)
    finalized = [chapter(b.id, no, "finalized") for no in range(1, 8)]
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        RowListResult([(no, node(b.id, ov.id, no).id) for no in range(1, 9)]),
        ScalarListResult(finalized),
    )

    decision = await select_next_chapter(db, b.id)

    assert decision.chapter_no == 8
    assert decision.action == "create_chapter"


@pytest.mark.asyncio
async def test_failed_chapter_four_is_reused_before_next_number():
    b = book()
    ov = outline(b.id)
    c4 = chapter(b.id, 4, "failed")
    nodes = {no: node(b.id, ov.id, no) for no in range(1, 5)}
    finalized = [chapter(b.id, no, "finalized") for no in range(1, 4)]
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        RowListResult([(no, nodes[no].id) for no in range(1, 5)]),
        ScalarListResult(finalized + [c4]),
        ScalarResult(None),  # no active run
    )

    decision = await select_next_chapter(db, b.id)

    assert decision.action == "resume_unfinished"
    assert decision.chapter_no == 4
    assert decision.chapter_id == c4.id
    # The decision carries the CURRENT approved node for rebinding.
    assert decision.outline_node_id == nodes[4].id


@pytest.mark.asyncio
async def test_active_run_is_opened_without_creating_another():
    b = book()
    ov = outline(b.id)
    c4 = chapter(b.id, 4, "running")
    nodes = {no: node(b.id, ov.id, no) for no in range(1, 5)}
    finalized = [chapter(b.id, no, "finalized") for no in range(1, 4)]
    active = run(c4.id)
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        RowListResult([(no, nodes[no].id) for no in range(1, 5)]),
        ScalarListResult(finalized + [c4]),
        ScalarResult(active),
    )

    decision = await select_next_chapter(db, b.id)

    assert decision.action == "open_active_run"
    assert decision.active_run_id == active.id
    assert decision.chapter_no == 4


@pytest.mark.asyncio
async def test_outline_numbering_gap_is_blocked():
    """A hole in the approved outline numbering is OUTLINE_NODE_MISSING."""
    b = book()
    ov = outline(b.id)
    n2 = node(b.id, ov.id, 2)
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        RowListResult([(2, n2.id)]),  # node numbering starts at 2: gap at 1
        ScalarListResult([]),
    )

    with pytest.raises(NextChapterSelectionError) as exc:
        await select_next_chapter(db, b.id)

    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "OUTLINE_NODE_MISSING"
    assert exc.value.detail["chapter_no"] == 1


@pytest.mark.asyncio
async def test_unapproved_outline_is_blocked_without_fallback():
    b = book()
    db = db_for(ScalarResult(b), ScalarResult(None))

    with pytest.raises(NextChapterSelectionError) as exc:
        await select_next_chapter(db, b.id)

    assert exc.value.detail["code"] == "OUTLINE_NOT_APPROVED"


@pytest.mark.asyncio
async def test_intermediate_state_without_active_run_fails_closed():
    """A reviewing chapter with no live run must never yield create_chapter."""
    b = book()
    ov = outline(b.id)
    c1 = chapter(b.id, 1, "reviewing")
    n1 = node(b.id, ov.id, 1)
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        RowListResult([(1, n1.id)]),
        ScalarListResult([c1]),
        ScalarResult(None),  # no active run
    )

    with pytest.raises(NextChapterSelectionError) as exc:
        await select_next_chapter(db, b.id)

    assert exc.value.detail["code"] == "CHAPTER_STATE_INCONSISTENT"
    assert exc.value.detail["chapter_no"] == 1


@pytest.mark.asyncio
async def test_high_numbered_test_chapter_is_ignored():
    b = book()
    ov = outline(b.id)
    n1 = node(b.id, ov.id, 1)
    test_chapter = chapter(b.id, 9999, "queued")
    db = db_for(
        ScalarResult(b),
        ScalarResult(ov),
        RowListResult([(1, n1.id)]),
        # The selector's chapter_no filter excludes the synthetic chapter.
        ScalarListResult([]),
    )

    decision = await select_next_chapter(db, b.id)

    assert decision.chapter_no == 1
    assert decision.chapter_id is None
    assert decision.action == "create_chapter"
    assert test_chapter.chapter_no == 9999
