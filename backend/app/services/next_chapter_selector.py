"""Deterministic selection of the next writable chapter.

The selector is deliberately independent from chapter creation. It only chooses an
approved outline node and reports whether an existing chapter/run should be reused.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Book, Chapter, ChapterRun, OutlineNode, OutlineVersion
from app.state_machine import ChapterState


NextChapterAction = Literal[
    "create_chapter",
    "resume_unfinished",
    "open_active_run",
    "needs_human",
]

# A run in these states owns the chapter and must be opened rather than duplicated.
ACTIVE_RUN_STATES = frozenset(
    {
        "queued",
        "running",
        "paused",
        "waiting_dependency",
        "retryable",
    }
)

# These are unfinished chapter states eligible for the next-chapter workflow.
UNFINISHED_CHAPTER_STATES = frozenset(
    {
        ChapterState.QUEUED.value,
        "running",
        "paused",
        "retryable",
        ChapterState.FAILED.value,
        ChapterState.NEEDS_HUMAN.value,
        ChapterState.RESOURCE_BLOCKED.value,
        ChapterState.BLOCKED_BY_DEPENDENCY.value,
        "waiting_dependency",
    }
)


@dataclass(frozen=True)
class NextChapterDecision:
    book_id: UUID
    chapter_no: int
    chapter_id: UUID | None
    outline_node_id: UUID
    action: NextChapterAction
    active_run_id: UUID | None
    reason: str
    outline_version_id: UUID


class NextChapterSelectionError(HTTPException):
    """A deterministic selection failure that can be exposed as a structured API error."""

    def __init__(self, code: str, chapter_no: int | None, message: str, status_code: int = 422):
        detail = {"code": code, "message": message}
        if chapter_no is not None:
            detail["chapter_no"] = chapter_no
        super().__init__(status_code=status_code, detail=detail)


async def select_next_chapter(
    db: AsyncSession,
    book_id: UUID,
    *,
    request_id: str | None = None,
) -> NextChapterDecision:
    """Select the only chapter the next-chapter workflow is allowed to write.

    The caller keeps the transaction open after this function returns. The book row
    lock serializes concurrent next-chapter requests for the same book.
    """
    book = (
        await db.execute(select(Book).where(Book.id == book_id).with_for_update())
    ).scalar_one_or_none()
    if book is None:
        raise NextChapterSelectionError("BOOK_NOT_FOUND", None, "作品不存在", status_code=404)

    outline_version = (
        await db.execute(
            select(OutlineVersion)
            .where(
                OutlineVersion.book_id == book_id,
                OutlineVersion.status == "approved",
            )
            .order_by(OutlineVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if outline_version is None:
        raise NextChapterSelectionError(
            "OUTLINE_NOT_APPROVED",
            None,
            "请先批准大纲后再写作",
        )

    approved_node_ids = select(OutlineNode.id).where(
        OutlineNode.book_id == book_id,
        OutlineNode.outline_version_id == outline_version.id,
    )
    unfinished = (
        await db.execute(
            select(Chapter)
            .where(
                Chapter.book_id == book_id,
                Chapter.status.in_(UNFINISHED_CHAPTER_STATES),
                Chapter.outline_node_id.in_(approved_node_ids),
            )
            .order_by(Chapter.chapter_no.asc())
        )
    ).scalars().all()

    if unfinished:
        chapter = unfinished[0]
        chapter_no = int(chapter.chapter_no)
        existing_node = (
            await db.execute(
                select(OutlineNode)
                .where(
                    OutlineNode.book_id == book_id,
                    OutlineNode.outline_version_id == outline_version.id,
                    OutlineNode.chapter_no == chapter_no,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing_node is None:
            raise NextChapterSelectionError(
                "OUTLINE_NODE_MISSING",
                chapter_no,
                f"第{chapter_no}章没有已批准章纲",
            )

        active_run = (
            await db.execute(
                select(ChapterRun)
                .where(
                    ChapterRun.chapter_id == chapter.id,
                    ChapterRun.status.in_(ACTIVE_RUN_STATES),
                )
                .order_by(ChapterRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if active_run is not None:
            return NextChapterDecision(
                book_id=book_id,
                chapter_no=chapter_no,
                chapter_id=chapter.id,
                outline_node_id=existing_node.id,
                action="open_active_run",
                active_run_id=active_run.id,
                reason="existing chapter has an active run",
                outline_version_id=outline_version.id,
            )

        if chapter.status == ChapterState.NEEDS_HUMAN.value:
            action: NextChapterAction = "needs_human"
            reason = "unfinished chapter requires human intervention"
        else:
            action = "resume_unfinished"
            reason = "reuse the smallest unfinished chapter"
        return NextChapterDecision(
            book_id=book_id,
            chapter_no=chapter_no,
            chapter_id=chapter.id,
            outline_node_id=existing_node.id,
            action=action,
            active_run_id=None,
            reason=reason,
            outline_version_id=outline_version.id,
        )

    finalized_no = (
        await db.execute(
            select(Chapter.chapter_no)
            .where(
                Chapter.book_id == book_id,
                Chapter.status == ChapterState.FINALIZED.value,
            )
            .order_by(Chapter.chapter_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    chapter_no = int(finalized_no or 0) + 1

    node = (
        await db.execute(
            select(OutlineNode)
            .where(
                OutlineNode.book_id == book_id,
                OutlineNode.outline_version_id == outline_version.id,
                OutlineNode.chapter_no == chapter_no,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if node is None:
        raise NextChapterSelectionError(
            "OUTLINE_NODE_MISSING",
            chapter_no,
            f"第{chapter_no}章没有已批准章纲",
        )

    return NextChapterDecision(
        book_id=book_id,
        chapter_no=chapter_no,
        chapter_id=None,
        outline_node_id=node.id,
        action="create_chapter",
        active_run_id=None,
        reason="next number after the highest finalized chapter",
        outline_version_id=outline_version.id,
    )


__all__ = [
    "ACTIVE_RUN_STATES",
    "NextChapterDecision",
    "NextChapterSelectionError",
    "UNFINISHED_CHAPTER_STATES",
    "select_next_chapter",
]
