"""v9.4: session quality/metric guards over editorial data (spec §23–§27).

These metrics deliberately do NOT reuse dashboard `window_good_rate`
— the session guard only reads first-round (round_no=1) human verdicts,
and the backlog count includes revision-requested/revising chapters.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Chapter, EditorialAnnotation, EditorialReviewRound

# Backlog states per spec §23 — any chapter still inside the review loop.
BACKLOG_STATUSES = frozenset(
    {"pending_review", "in_review", "revision_requested", "revising", "awaiting_recheck"}
)

BAD_FIRST_ROUND_VERDICTS = frozenset({"revise", "reject"})


async def count_editorial_backlog(db: AsyncSession, book_id) -> int:
    """How many chapters are still inside the review loop (spec §23)."""
    result = await db.execute(
        select(Chapter.id).where(
            Chapter.book_id == book_id,
            func.coalesce(Chapter.editorial_status, "pending_review").in_(BACKLOG_STATUSES),
        )
    )
    return len(result.scalars().all())


async def recent_first_pass_yield(
    db: AsyncSession,
    *,
    book_id,
    window_size: int = 10,
) -> dict:
    """First-round yield over the most recent N chapters with a submitted round 1."""
    rounds = list(
        (
            await db.execute(
                select(EditorialReviewRound).where(
                    EditorialReviewRound.book_id == book_id,
                    EditorialReviewRound.round_no == 1,
                    EditorialReviewRound.status == "submitted",
                )
            )
        ).scalars()
    )
    # One round-1 per chapter; order by chapter_no via chapter join
    chapter_nos = {}
    if rounds:
        chapters = list(
            (
                await db.execute(
                    select(Chapter.id, Chapter.chapter_no).where(
                        Chapter.book_id == book_id,
                        Chapter.id.in_({r.chapter_id for r in rounds}),
                    )
                )
            ).all()
        )
        chapter_nos = {cid: no for cid, no in chapters}

    def sort_key(r):
        return (chapter_nos.get(r.chapter_id, 0), r.submitted_at or r.created_at)

    rounds.sort(key=sort_key)
    window = rounds[-window_size:] if window_size > 0 else rounds

    blocking = set()
    if window:
        anns = list(
            (
                await db.execute(
                    select(EditorialAnnotation.chapter_id).where(
                        EditorialAnnotation.book_id == book_id,
                        EditorialAnnotation.chapter_id.in_({r.chapter_id for r in window}),
                        EditorialAnnotation.is_blocking.is_(True),
                    )
                )
            ).scalars()
        )
        blocking = set(anns)

    good = 0
    for r in window:
        if r.verdict == "accept":
            good += 1
        elif r.verdict == "accept_with_notes" and r.chapter_id not in blocking:
            good += 1

    reviewed = len(window)
    return {
        "reviewed": reviewed,
        "good": good,
        "rate": (good / reviewed) if reviewed else 0.0,
    }


async def recent_consecutive_bad_first_rounds(db: AsyncSession, book_id) -> int:
    """Number of consecutive (latest-first) chapters whose round-1 verdict is bad."""
    rounds = list(
        (
            await db.execute(
                select(EditorialReviewRound).where(
                    EditorialReviewRound.book_id == book_id,
                    EditorialReviewRound.round_no == 1,
                    EditorialReviewRound.status == "submitted",
                )
            )
        ).scalars()
    )
    if not rounds:
        return 0
    chapter_nos = {}
    chapters = list(
        (
            await db.execute(
                select(Chapter.id, Chapter.chapter_no).where(
                    Chapter.book_id == book_id,
                    Chapter.id.in_({r.chapter_id for r in rounds}),
                )
            )
        ).all()
    )
    chapter_nos = {cid: no for cid, no in chapters}
    rounds.sort(key=lambda r: (chapter_nos.get(r.chapter_id, 0), r.submitted_at or r.created_at))
    consecutive = 0
    for r in reversed(rounds):
        if r.verdict in BAD_FIRST_ROUND_VERDICTS:
            consecutive += 1
        else:
            break
    return consecutive
