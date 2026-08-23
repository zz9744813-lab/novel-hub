"""v9.3 Editorial Learning Loop REST API (spec §82–§90).

Human review is ground truth: every chapter finalized by the AI pipeline
enters `pending_review` and is graded, annotated and dispositioned here.
Fail-closed: invalid anchors, unknown rubric keys and illegal verdicts are
422/409, never silently normalized.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.editorial.anchoring import build_anchor, split_paragraphs
from app.editorial.rubric import (
    get_or_create_policy,
    resolve_rubric,
    score_to_grade,
    validate_rubric_scores,
)
from app.models.tables import (
    Chapter,
    ChapterVersion,
    EditorialAnnotation,
    EditorialExperienceCard,
    EditorialFeedbackInsight,
    EditorialReviewPolicy,
    EditorialReviewRound,
    ReviewIssue,
)

logger = logging.getLogger("novelforge.editorial_api")

router = APIRouter(prefix="/api", tags=["editorial"])

VERDICTS = {"accept", "accept_with_notes", "revise", "reject"}
ANNOTATION_TYPES = {
    "issue", "suggestion", "direct_edit", "praise",
    "question", "preference", "forbidden_pattern",
}
SEVERITIES = {"critical", "major", "minor", "note", "praise"}
SCOPES = {
    "local_span", "scene", "chapter", "character",
    "scene_type", "book_future", "global",
}
DISPOSITIONS = {"confirm": "confirmed", "dismiss": "dismissed", "correct": "corrected"}

QUEUE_FILTERS = {"pending", "recheck", "accepted", "rejected", "all"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── schemas ───────────────────────────────────────────────────────────


class PolicyOut(BaseModel):
    book_id: str
    mode: str
    max_unreviewed_ahead: int
    review_sampling_mode: str
    require_review: bool
    good_score_threshold: int
    auto_pause_good_rate_threshold: int
    auto_pause_consecutive_bad: int
    rubric_template_id: str | None
    experience_auto_activation: bool
    low_risk_auto_promote: bool


class PolicyUpdate(BaseModel):
    mode: str | None = None
    max_unreviewed_ahead: int | None = None
    review_sampling_mode: str | None = None
    require_review: bool | None = None
    good_score_threshold: int | None = None
    auto_pause_good_rate_threshold: int | None = None
    auto_pause_consecutive_bad: int | None = None
    experience_auto_activation: bool | None = None
    low_risk_auto_promote: bool | None = None


class RubricDimension(BaseModel):
    key: str
    name: str
    weight: int
    anchors: dict


class ReviewRoundOut(BaseModel):
    id: str
    book_id: str
    chapter_id: str
    chapter_version_id: str
    round_no: int
    status: str
    verdict: str | None
    score_total: int | None
    grade: str | None
    rubric_scores: dict | None
    overall_comment: str | None
    reviewer_kind: str
    reviewer_id: str | None
    ai_issue_dispositions: dict
    submitted_at: datetime | None
    completed_at: datetime | None


class AnnotationIn(BaseModel):
    annotation_type: str
    category: str | None = None
    severity: str | None = None
    scope: str = "local_span"
    scene_no: int | None = None
    paragraph_key: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    quoted_text: str | None = None
    comment: str | None = None
    suggested_text: str | None = None
    is_blocking: bool = False
    tags: list[str] = Field(default_factory=list)


class AnnotationPatch(BaseModel):
    category: str | None = None
    severity: str | None = None
    scope: str | None = None
    comment: str | None = None
    suggested_text: str | None = None
    is_blocking: bool | None = None
    tags: list[str] | None = None
    resolution_status: str | None = None


class AnnotationOut(BaseModel):
    id: str
    review_round_id: str
    annotation_type: str
    category: str | None
    severity: str
    scope: str
    scene_no: int | None
    paragraph_key: int | None
    start_offset: int | None
    end_offset: int | None
    quoted_text: str | None
    comment: str | None
    suggested_text: str | None
    is_blocking: bool
    ai_issue_match_ids: list
    tags: list
    resolution_status: str
    resolved_by_version_id: str | None


class RoundSubmitIn(BaseModel):
    verdict: str
    score_total: int | None = None
    rubric_scores: dict | None = None
    quick_grade: str | None = None  # A/B/C/D quick mode (spec §12)
    overall_comment: str | None = None


class AiIssueOut(BaseModel):
    id: str
    issue_type: str
    severity: str
    evidence: str
    paragraph_id: str
    repair_instruction: str | None
    disposition: str | None = None


class ReviewDetailOut(BaseModel):
    round: ReviewRoundOut
    chapter: dict
    version_content: str
    paragraphs: list[str]
    rubric: list[dict]
    annotations: list[AnnotationOut]
    ai_issues: list[AiIssueOut]
    version_lineage: list[dict]


class QueueCardOut(BaseModel):
    chapter_id: str
    book_id: str
    book_title: str | None
    chapter_no: int
    title: str | None
    editorial_status: str
    ai_status: str
    latest_version_id: str | None
    ai_issue_count: int
    waiting_hours: float
    rounds: int


# ── helpers ───────────────────────────────────────────────────────────


async def _get_book_chapter(db: AsyncSession, chapter_id: uuid.UUID) -> Chapter:
    chapter = (
        await db.execute(select(Chapter).where(Chapter.id == chapter_id))
    ).scalar_one_or_none()
    if chapter is None:
        raise HTTPException(status_code=404, detail="chapter not found")
    return chapter


async def _get_round(db: AsyncSession, review_id: uuid.UUID) -> EditorialReviewRound:
    rnd = (
        await db.execute(
            select(EditorialReviewRound).where(EditorialReviewRound.id == review_id)
        )
    ).scalar_one_or_none()
    if rnd is None:
        raise HTTPException(status_code=404, detail="review round not found")
    return rnd


async def _latest_version(db: AsyncSession, chapter_id: uuid.UUID) -> ChapterVersion | None:
    return (
        await db.execute(
            select(ChapterVersion)
            .where(ChapterVersion.chapter_id == chapter_id)
            .order_by(ChapterVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _enqueue_editorial_job(job_name: str, *args, job_id: str | None = None) -> None:
    import redis.asyncio.connection as _rc

    _rc.AbstractConnection.lib_name = None
    _rc.AbstractConnection.lib_version = None
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    parts = redis_url.replace("redis://", "").split(":")
    host = parts[0]
    port = int(parts[1].split("/")[0]) if len(parts) > 1 else 6379
    pool = await create_pool(RedisSettings(host=host, port=port))
    try:
        await pool.enqueue_job(job_name, *args, _job_id=job_id)
    finally:
        await pool.close()


def _round_out(r: EditorialReviewRound) -> ReviewRoundOut:
    return ReviewRoundOut(
        id=str(r.id),
        book_id=str(r.book_id),
        chapter_id=str(r.chapter_id),
        chapter_version_id=str(r.chapter_version_id),
        round_no=r.round_no,
        status=r.status,
        verdict=r.verdict,
        score_total=r.score_total,
        grade=r.grade,
        rubric_scores=r.rubric_scores_json,
        overall_comment=r.overall_comment,
        reviewer_kind=r.reviewer_kind,
        reviewer_id=r.reviewer_id,
        ai_issue_dispositions=r.ai_issue_dispositions or {},
        submitted_at=r.submitted_at,
        completed_at=r.completed_at,
    )


def _ann_out(a: EditorialAnnotation) -> AnnotationOut:
    return AnnotationOut(
        id=str(a.id),
        review_round_id=str(a.review_round_id),
        annotation_type=a.annotation_type,
        category=a.category,
        severity=a.severity,
        scope=a.scope,
        scene_no=a.scene_no,
        paragraph_key=int(a.paragraph_key) if a.paragraph_key is not None else None,
        start_offset=a.start_offset,
        end_offset=a.end_offset,
        quoted_text=a.quoted_text,
        comment=a.comment,
        suggested_text=a.suggested_text,
        is_blocking=a.is_blocking,
        ai_issue_match_ids=a.ai_issue_match_ids or [],
        tags=a.tags or [],
        resolution_status=a.resolution_status,
        resolved_by_version_id=(
            str(a.resolved_by_version_id) if a.resolved_by_version_id else None
        ),
    )


# ── policy (spec §82) ─────────────────────────────────────────────────


@router.get("/books/{book_id}/editorial/policy", response_model=PolicyOut)
async def get_policy(book_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> PolicyOut:
    p = await get_or_create_policy(db, book_id)
    return PolicyOut(
        book_id=str(p.book_id),
        mode=p.mode,
        max_unreviewed_ahead=p.max_unreviewed_ahead,
        review_sampling_mode=p.review_sampling_mode,
        require_review=p.require_review,
        good_score_threshold=p.good_score_threshold,
        auto_pause_good_rate_threshold=p.auto_pause_good_rate_threshold,
        auto_pause_consecutive_bad=p.auto_pause_consecutive_bad,
        rubric_template_id=str(p.rubric_template_id) if p.rubric_template_id else None,
        experience_auto_activation=p.experience_auto_activation,
        low_risk_auto_promote=p.low_risk_auto_promote,
    )


@router.put("/books/{book_id}/editorial/policy", response_model=PolicyOut)
async def update_policy(
    book_id: uuid.UUID,
    req: PolicyUpdate,
    db: AsyncSession = Depends(get_db),
) -> PolicyOut:
    p = await get_or_create_policy(db, book_id)
    if req.mode is not None:
        if req.mode not in {"blocking", "windowed", "learning_only"}:
            raise HTTPException(status_code=422, detail="invalid mode")
        p.mode = req.mode
    if req.max_unreviewed_ahead is not None:
        if req.mode is None and p.mode != "windowed" and req.max_unreviewed_ahead != p.max_unreviewed_ahead:
            pass  # allowed: field stored even when mode ignores it
        if req.max_unreviewed_ahead < 0:
            raise HTTPException(status_code=422, detail="max_unreviewed_ahead must be >= 0")
        p.max_unreviewed_ahead = req.max_unreviewed_ahead
    if req.review_sampling_mode is not None:
        if req.review_sampling_mode not in {"all", "risk_based", "random", "hybrid"}:
            raise HTTPException(status_code=422, detail="invalid review_sampling_mode")
        p.review_sampling_mode = req.review_sampling_mode
    if req.require_review is not None:
        p.require_review = req.require_review
    if req.good_score_threshold is not None:
        p.good_score_threshold = req.good_score_threshold
    if req.auto_pause_good_rate_threshold is not None:
        p.auto_pause_good_rate_threshold = req.auto_pause_good_rate_threshold
    if req.auto_pause_consecutive_bad is not None:
        p.auto_pause_consecutive_bad = req.auto_pause_consecutive_bad
    if req.experience_auto_activation is not None:
        p.experience_auto_activation = req.experience_auto_activation
    if req.low_risk_auto_promote is not None:
        p.low_risk_auto_promote = req.low_risk_auto_promote
    await db.commit()
    return await get_policy(book_id, db)


# ── review queue (spec §83, §61) ──────────────────────────────────────


async def _queue_cards(db: AsyncSession, book_id: uuid.UUID | None, status_filter: str):
    from app.models.tables import Book

    stmt = select(Chapter).where(Chapter.status == "finalized")
    if book_id is not None:
        stmt = stmt.where(Chapter.book_id == book_id)
    status_map = {
        "pending": {"pending_review", "in_review"},
        "recheck": {"awaiting_recheck", "revising"},
        "accepted": {"accepted", "accepted_with_notes", "waived"},
        "rejected": {"rejected", "revision_requested"},
        "all": None,
    }
    allowed = status_map.get(status_filter)
    if allowed is not None:
        stmt = stmt.where(Chapter.editorial_status.in_(allowed))
    chapters = (await db.execute(stmt.order_by(Chapter.chapter_no))).scalars().all()

    cards: list[QueueCardOut] = []
    for ch in chapters:
        version = await _latest_version(db, ch.id)
        issue_count = (
            await db.execute(
                select(func.count(ReviewIssue.id)).where(ReviewIssue.chapter_id == ch.id)
            )
        ).scalar_one()
        rounds = (
            await db.execute(
                select(func.count(EditorialReviewRound.id)).where(
                    EditorialReviewRound.chapter_id == ch.id
                )
            )
        ).scalar_one()
        book_title = None
        if book_id is None:
            book = (
                await db.execute(select(Book).where(Book.id == ch.book_id))
            ).scalar_one_or_none()
            book_title = getattr(book, "title", None)
        created = version.created_at if version else ch.created_at
        waiting = (
            (_now() - created).total_seconds() / 3600 if created is not None else 0.0
        )
        cards.append(
            QueueCardOut(
                chapter_id=str(ch.id),
                book_id=str(ch.book_id),
                book_title=book_title,
                chapter_no=ch.chapter_no,
                title=ch.title,
                editorial_status=ch.editorial_status,
                ai_status=ch.status,
                latest_version_id=str(version.id) if version else None,
                ai_issue_count=int(issue_count),
                waiting_hours=round(waiting, 1),
                rounds=int(rounds),
            )
        )
    return cards


@router.get("/editorial/review-queue", response_model=list[QueueCardOut])
async def global_review_queue(
    filter: str = "pending",
    db: AsyncSession = Depends(get_db),
) -> list[QueueCardOut]:
    if filter not in QUEUE_FILTERS:
        raise HTTPException(status_code=422, detail=f"filter must be one of {sorted(QUEUE_FILTERS)}")
    return await _queue_cards(db, None, filter)


@router.get("/books/{book_id}/editorial/review-queue", response_model=list[QueueCardOut])
async def book_review_queue(
    book_id: uuid.UUID,
    filter: str = "pending",
    db: AsyncSession = Depends(get_db),
) -> list[QueueCardOut]:
    if filter not in QUEUE_FILTERS:
        raise HTTPException(status_code=422, detail=f"filter must be one of {sorted(QUEUE_FILTERS)}")
    return await _queue_cards(db, book_id, filter)


# ── review rounds (spec §84) ──────────────────────────────────────────


@router.post("/chapters/{chapter_id}/editorial/reviews", response_model=ReviewRoundOut, status_code=201)
async def create_review_round(
    chapter_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ReviewRoundOut:
    chapter = await _get_book_chapter(db, chapter_id)
    if chapter.editorial_status in {"accepted", "accepted_with_notes", "waived"}:
        # re-review after revision is allowed via awaiting_recheck; otherwise
        # opening a new round on an accepted chapter is a client bug
        raise HTTPException(
            status_code=409,
            detail=f"chapter already in terminal editorial state '{chapter.editorial_status}'",
        )
    version = await _latest_version(db, chapter_id)
    if version is None:
        raise HTTPException(status_code=409, detail="chapter has no versions to review")

    next_no = (
        await db.execute(
            select(func.max(EditorialReviewRound.round_no)).where(
                EditorialReviewRound.chapter_id == chapter_id
            )
        )
    ).scalar_one()
    rnd = EditorialReviewRound(
        book_id=chapter.book_id,
        chapter_id=chapter_id,
        chapter_version_id=version.id,
        round_no=(next_no or 0) + 1,
        status="draft",
        reviewer_kind="human",
        ai_issue_dispositions={},
    )
    db.add(rnd)
    chapter.editorial_status = "in_review"
    await db.flush()
    await db.commit()
    return _round_out(rnd)


@router.get("/chapters/{chapter_id}/editorial/reviews", response_model=list[ReviewRoundOut])
async def list_review_rounds(
    chapter_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[ReviewRoundOut]:
    rows = (
        await db.execute(
            select(EditorialReviewRound)
            .where(EditorialReviewRound.chapter_id == chapter_id)
            .order_by(EditorialReviewRound.round_no)
        )
    ).scalars().all()
    return [_round_out(r) for r in rows]


@router.get("/editorial/reviews/{review_id}", response_model=ReviewDetailOut)
async def get_review_detail(
    review_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ReviewDetailOut:
    rnd = await _get_round(db, review_id)
    chapter = await _get_book_chapter(db, rnd.chapter_id)
    version = (
        await db.execute(
            select(ChapterVersion).where(ChapterVersion.id == rnd.chapter_version_id)
        )
    ).scalar_one()
    paragraphs = split_paragraphs(version.content or "")
    annotations = (
        await db.execute(
            select(EditorialAnnotation)
            .where(EditorialAnnotation.review_round_id == rnd.id)
            .order_by(EditorialAnnotation.created_at)
        )
    ).scalars().all()
    issues = (
        await db.execute(
            select(ReviewIssue).where(ReviewIssue.chapter_id == rnd.chapter_id)
        )
    ).scalars().all()
    dispositions = rnd.ai_issue_dispositions or {}

    lineage_rows = (
        await db.execute(
            select(ChapterVersion)
            .where(ChapterVersion.chapter_id == rnd.chapter_id)
            .order_by(ChapterVersion.version)
        )
    ).scalars().all()
    lineage = [
        {
            "id": str(v.id),
            "version": v.version,
            "version_kind": v.version_kind,
            "revision_origin": v.revision_origin,
            "parent_version_id": str(v.parent_version_id) if v.parent_version_id else None,
            "editorial_review_round_id": (
                str(v.editorial_review_round_id) if v.editorial_review_round_id else None
            ),
            "word_count": v.word_count,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in lineage_rows
    ]

    return ReviewDetailOut(
        round=_round_out(rnd),
        chapter={
            "chapter_id": str(chapter.id),
            "chapter_no": chapter.chapter_no,
            "title": chapter.title,
            "editorial_status": chapter.editorial_status,
            "ai_status": chapter.status,
        },
        version_content=version.content or "",
        paragraphs=paragraphs,
        rubric=await resolve_rubric(db, rnd.book_id),
        annotations=[_ann_out(a) for a in annotations],
        ai_issues=[
            AiIssueOut(
                id=str(i.id),
                issue_type=i.issue_type,
                severity=i.severity,
                evidence=i.evidence,
                paragraph_id=i.paragraph_id,
                repair_instruction=i.repair_instruction,
                disposition=dispositions.get(str(i.id)),
            )
            for i in issues
        ],
        version_lineage=lineage,
    )


@router.post("/editorial/reviews/{review_id}/submit", response_model=ReviewRoundOut)
async def submit_review_round(
    review_id: uuid.UUID,
    req: RoundSubmitIn,
    db: AsyncSession = Depends(get_db),
) -> ReviewRoundOut:
    rnd = await _get_round(db, review_id)
    if rnd.status == "submitted":
        raise HTTPException(status_code=409, detail="review round already submitted")
    if req.verdict not in VERDICTS:
        raise HTTPException(status_code=422, detail=f"verdict must be one of {sorted(VERDICTS)}")

    chapter = await _get_book_chapter(db, rnd.chapter_id)

    # scoring: rubric detail mode wins over quick grade (spec §12)
    score_total = req.score_total
    if req.rubric_scores:
        rubric = await resolve_rubric(db, rnd.book_id)
        try:
            score_total = validate_rubric_scores(req.rubric_scores, rubric)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        rnd.rubric_scores_json = req.rubric_scores
    elif req.quick_grade:
        if req.quick_grade not in {"A", "B", "C", "D"}:
            raise HTTPException(status_code=422, detail="quick_grade must be A/B/C/D")
        band = {"A": 95, "B": 85, "C": 75, "D": 60}[req.quick_grade]
        score_total = score_total if score_total is not None else band

    rnd.verdict = req.verdict
    rnd.score_total = score_total
    rnd.grade = req.quick_grade or score_to_grade(score_total)
    rnd.overall_comment = req.overall_comment
    rnd.status = "submitted"
    rnd.submitted_at = _now()
    rnd.completed_at = _now()

    status_map = {
        "accept": "accepted",
        "accept_with_notes": "accepted_with_notes",
        "revise": "revision_requested",
        "reject": "rejected",
    }
    chapter.editorial_status = status_map[req.verdict]
    await db.commit()

    # fire-and-forget analysis; worker owns failure handling
    try:
        await _enqueue_editorial_job(
            "analyze_editorial_review_job", str(review_id), _job_id=f"ell:analyze:{review_id}"
        )
    except Exception:  # noqa: BLE001 - queue unavailable must not lose the verdict
        logger.warning("failed to enqueue analysis for review %s", review_id, exc_info=True)

    return _round_out(rnd)


# ── annotations (spec §85, §8, §9, §17) ───────────────────────────────


@router.post("/editorial/reviews/{review_id}/annotations", response_model=AnnotationOut, status_code=201)
async def create_annotation(
    review_id: uuid.UUID,
    req: AnnotationIn,
    db: AsyncSession = Depends(get_db),
) -> AnnotationOut:
    rnd = await _get_round(db, review_id)
    if rnd.status == "submitted":
        raise HTTPException(status_code=409, detail="cannot annotate a submitted round")

    if req.annotation_type not in ANNOTATION_TYPES:
        raise HTTPException(status_code=422, detail=f"annotation_type must be one of {sorted(ANNOTATION_TYPES)}")
    if req.scope not in SCOPES:
        raise HTTPException(status_code=422, detail=f"scope must be one of {sorted(SCOPES)}")
    severity = req.severity
    if severity is not None and severity not in SEVERITIES:
        raise HTTPException(status_code=422, detail=f"severity must be one of {sorted(SEVERITIES)}")
    if req.annotation_type == "praise":
        severity = "praise"
    if severity is None:
        severity = "praise" if req.annotation_type == "praise" else "minor"
    if req.annotation_type == "direct_edit" and not req.suggested_text:
        raise HTTPException(status_code=422, detail="direct_edit requires suggested_text")

    quote_hash = ctx_before = ctx_after = ctx_hash = None
    paragraph_key_s = str(req.paragraph_key) if req.paragraph_key is not None else None
    if req.quoted_text:
        if req.paragraph_key is None:
            raise HTTPException(status_code=422, detail="quoted_text requires paragraph_key")
        version = (
            await db.execute(
                select(ChapterVersion).where(ChapterVersion.id == rnd.chapter_version_id)
            )
        ).scalar_one()
        paragraphs = split_paragraphs(version.content or "")
        if not (0 <= req.paragraph_key < len(paragraphs)):
            raise HTTPException(status_code=422, detail="paragraph_key out of range")
        para = paragraphs[req.paragraph_key]
        start = req.start_offset if req.start_offset is not None else para.find(req.quoted_text)
        end = req.end_offset if req.end_offset is not None else start + len(req.quoted_text)
        if start < 0 or end > len(para) or para[start:end] != req.quoted_text:
            raise HTTPException(
                status_code=422,
                detail="quoted_text does not match paragraph content at offsets",
            )
        try:
            anchor = build_anchor(paragraphs, req.paragraph_key, req.quoted_text)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        quote_hash, ctx_before, ctx_after, ctx_hash = (
            anchor.quote_hash, anchor.context_before, anchor.context_after, anchor.context_hash,
        )
    elif req.paragraph_key is not None and not req.quoted_text:
        # chapter/scene-level comment anchored to a paragraph without a quote
        pass

    ann = EditorialAnnotation(
        review_round_id=rnd.id,
        book_id=rnd.book_id,
        chapter_id=rnd.chapter_id,
        chapter_version_id=rnd.chapter_version_id,
        annotation_type=req.annotation_type,
        category=req.category,
        severity=severity,
        scope=req.scope,
        scene_no=req.scene_no,
        paragraph_key=paragraph_key_s,
        start_offset=req.start_offset,
        end_offset=req.end_offset,
        quoted_text=req.quoted_text,
        quote_hash=quote_hash,
        context_before=ctx_before,
        context_after=ctx_after,
        context_hash=ctx_hash,
        comment=req.comment,
        suggested_text=req.suggested_text,
        is_blocking=req.is_blocking,
        tags=req.tags,
        ai_issue_match_ids=[],
        resolution_status="open",
    )
    db.add(ann)
    await db.flush()

    # spec §17: direct edit is the highest-value supervision sample
    if req.annotation_type == "direct_edit" and req.quoted_text and req.suggested_text:
        from app.models.tables import EditorialPreferencePair

        db.add(
            EditorialPreferencePair(
                book_id=rnd.book_id,
                chapter_id=rnd.chapter_id,
                review_round_id=rnd.id,
                annotation_id=ann.id,
                rejected_text=req.quoted_text,
                chosen_text=req.suggested_text,
                preference_reason=req.comment,
                category=req.category,
                scope=req.scope,
                source="human_direct_edit",
            )
        )
    await db.commit()
    return _ann_out(ann)


@router.patch("/editorial/annotations/{annotation_id}", response_model=AnnotationOut)
async def patch_annotation(
    annotation_id: uuid.UUID,
    req: AnnotationPatch,
    db: AsyncSession = Depends(get_db),
) -> AnnotationOut:
    ann = (
        await db.execute(
            select(EditorialAnnotation).where(EditorialAnnotation.id == annotation_id)
        )
    ).scalar_one_or_none()
    if ann is None:
        raise HTTPException(status_code=404, detail="annotation not found")
    if req.severity is not None:
        if req.severity not in SEVERITIES:
            raise HTTPException(status_code=422, detail="invalid severity")
        ann.severity = req.severity
    if req.scope is not None:
        if req.scope not in SCOPES:
            raise HTTPException(status_code=422, detail="invalid scope")
        ann.scope = req.scope
    if req.category is not None:
        ann.category = req.category
    if req.comment is not None:
        ann.comment = req.comment
    if req.suggested_text is not None:
        ann.suggested_text = req.suggested_text
    if req.is_blocking is not None:
        ann.is_blocking = req.is_blocking
    if req.tags is not None:
        ann.tags = req.tags
    if req.resolution_status is not None:
        if req.resolution_status not in {"open", "resolved", "unresolved", "moved"}:
            raise HTTPException(status_code=422, detail="invalid resolution_status")
        ann.resolution_status = req.resolution_status
    await db.commit()
    return _ann_out(ann)


@router.delete("/editorial/annotations/{annotation_id}", status_code=204)
async def delete_annotation(
    annotation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    ann = (
        await db.execute(
            select(EditorialAnnotation).where(EditorialAnnotation.id == annotation_id)
        )
    ).scalar_one_or_none()
    if ann is None:
        raise HTTPException(status_code=404, detail="annotation not found")
    await db.delete(ann)
    await db.commit()


# ── AI issue disposition (spec §86, §19–§21) ──────────────────────────


async def _disposition(
    db: AsyncSession, review_id: uuid.UUID, issue_id: uuid.UUID, action: str
) -> ReviewRoundOut:
    rnd = await _get_round(db, review_id)
    if rnd.status == "submitted" and action != "correct":
        raise HTTPException(status_code=409, detail="round already submitted")
    issue = (
        await db.execute(select(ReviewIssue).where(ReviewIssue.id == issue_id))
    ).scalar_one_or_none()
    if issue is None or issue.chapter_id != rnd.chapter_id:
        raise HTTPException(status_code=404, detail="AI issue not found for this round")
    dispositions = dict(rnd.ai_issue_dispositions or {})
    dispositions[str(issue_id)] = DISPOSITIONS[action]
    rnd.ai_issue_dispositions = dispositions
    await db.commit()
    return _round_out(rnd)


@router.post("/editorial/reviews/{review_id}/ai-issues/{issue_id}/confirm", response_model=ReviewRoundOut)
async def confirm_ai_issue(
    review_id: uuid.UUID, issue_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ReviewRoundOut:
    return await _disposition(db, review_id, issue_id, "confirm")


@router.post("/editorial/reviews/{review_id}/ai-issues/{issue_id}/dismiss", response_model=ReviewRoundOut)
async def dismiss_ai_issue(
    review_id: uuid.UUID, issue_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ReviewRoundOut:
    return await _disposition(db, review_id, issue_id, "dismiss")


@router.post("/editorial/reviews/{review_id}/ai-issues/{issue_id}/correct", response_model=ReviewRoundOut)
async def correct_ai_issue(
    review_id: uuid.UUID, issue_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ReviewRoundOut:
    return await _disposition(db, review_id, issue_id, "correct")


# ── revision (spec §87, §29, §30) ─────────────────────────────────────


class RevisionRequest(BaseModel):
    remediation_level: str | None = None  # L0..L5 override


@router.post("/editorial/reviews/{review_id}/revision")
async def request_revision(
    review_id: uuid.UUID,
    req: RevisionRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    rnd = await _get_round(db, review_id)
    if rnd.status != "submitted":
        raise HTTPException(status_code=409, detail="round must be submitted before revision")
    if rnd.verdict not in {"revise", "reject", "accept_with_notes"}:
        raise HTTPException(
            status_code=409,
            detail=f"verdict '{rnd.verdict}' does not request revision",
        )
    level = (req.remediation_level if req and req.remediation_level else None)
    if level is not None and level not in {f"L{i}" for i in range(6)}:
        raise HTTPException(status_code=422, detail="remediation_level must be L0..L5")

    chapter = await _get_book_chapter(db, rnd.chapter_id)
    chapter.editorial_status = "revising"
    await db.commit()
    try:
        await _enqueue_editorial_job(
            "run_editorial_revision_job",
            str(review_id),
            level,
            _job_id=f"ell:revise:{review_id}:{rnd.round_no}",
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"failed to enqueue revision: {e}")
    return {"status": "revising", "review_round_id": str(review_id)}


@router.get("/editorial/reviews/{review_id}/revision-status")
async def revision_status(
    review_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    rnd = await _get_round(db, review_id)
    chapter = await _get_book_chapter(db, rnd.chapter_id)
    version = await _latest_version(db, rnd.chapter_id)
    return {
        "editorial_status": chapter.editorial_status,
        "latest_version": (
            {
                "id": str(version.id),
                "version": version.version,
                "revision_origin": version.revision_origin,
                "parent_version_id": (
                    str(version.parent_version_id) if version.parent_version_id else None
                ),
            }
            if version
            else None
        ),
        "revised_from_round": str(rnd.id),
    }


# ── experience cards (spec §33–§40, PR-06) ────────────────────────────


class ExperienceCardOut(BaseModel):
    id: str
    book_id: str | None
    rule_type: str
    scope_type: str
    category: str
    trigger_conditions: dict
    instruction: str
    rationale: str | None
    target_components: list
    support_count: int
    contradiction_count: int
    confidence: float
    status: str
    is_locked: bool
    effective_from_chapter: int | None
    last_confirmed_at: datetime | None
    source_annotation_ids: list


def _card_out(c: EditorialExperienceCard) -> ExperienceCardOut:
    return ExperienceCardOut(
        id=str(c.id),
        book_id=str(c.book_id) if c.book_id else None,
        rule_type=c.rule_type,
        scope_type=c.scope_type,
        category=c.category,
        trigger_conditions=c.trigger_conditions or {},
        instruction=c.instruction,
        rationale=c.rationale,
        target_components=c.target_components or [],
        support_count=c.support_count,
        contradiction_count=c.contradiction_count,
        confidence=c.confidence,
        status=c.status,
        is_locked=c.is_locked,
        effective_from_chapter=c.effective_from_chapter,
        last_confirmed_at=c.last_confirmed_at,
        source_annotation_ids=c.source_annotation_ids or [],
    )


@router.get("/books/{book_id}/editorial/experience-cards")
async def list_experience_cards(
    book_id: uuid.UUID,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[ExperienceCardOut]:
    q = select(EditorialExperienceCard).where(
        (EditorialExperienceCard.book_id == book_id)
        | (EditorialExperienceCard.scope_type == "global")
    )
    if status:
        q = q.where(EditorialExperienceCard.status == status)
    rows = list((await db.execute(q.order_by(EditorialExperienceCard.support_count.desc()))).scalars())
    return [_card_out(c) for c in rows]


class CardStatusIn(BaseModel):
    status: str  # candidate|active|locked|superseded|rejected
    is_locked: bool | None = None


@router.patch("/editorial/experience-cards/{card_id}", response_model=ExperienceCardOut)
async def update_experience_card(
    card_id: uuid.UUID,
    req: CardStatusIn,
    db: AsyncSession = Depends(get_db),
) -> ExperienceCardOut:
    from app.editorial.experience import set_card_status

    try:
        card = await set_card_status(db, card_id, req.status, req.is_locked)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if card is None:
        raise HTTPException(status_code=404, detail="card not found")
    return _card_out(card)


class CardPreviewIn(BaseModel):
    chapter_no: int | None = None
    scene_type: str | None = None
    character_ids: list[str] = Field(default_factory=list)
    include_candidates: bool = False


@router.post("/books/{book_id}/editorial/experience-cards/preview")
async def preview_experience_injection(
    book_id: uuid.UUID,
    req: CardPreviewIn,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.editorial.experience import render_cards_for_prompt, retrieve_cards

    cards = await retrieve_cards(
        db,
        book_id,
        chapter_no=req.chapter_no,
        scene_type=req.scene_type,
        character_ids=req.character_ids,
        include_candidates=req.include_candidates,
    )
    return {
        "cards": [_card_out(c) for c in cards],
        "prompt_block": render_cards_for_prompt(cards),
    }


# ── improvement proposals + experiments (spec §43–§58, PR-09/10/11) ────


@router.get("/books/{book_id}/editorial/proposals")
async def list_proposals(
    book_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    from app.models.tables import EditorialImprovementProposal

    rows = list(
        (
            await db.execute(
                select(EditorialImprovementProposal)
                .where(EditorialImprovementProposal.book_id == book_id)
                .order_by(EditorialImprovementProposal.created_at.desc())
                .limit(100)
            )
        ).scalars()
    )
    return [
        {
            "id": str(p.id),
            "proposal_type": p.proposal_type,
            "target_component": p.target_component,
            "risk_level": p.risk_level,
            "reason": p.reason,
            "candidate_patch": p.candidate_patch or {},
            "status": p.status,
            "approved_by": p.approved_by,
            "approved_at": p.approved_at.isoformat() if p.approved_at else None,
            "experiment_id": str(p.experiment_id) if p.experiment_id else None,
            "promoted_at": p.promoted_at.isoformat() if p.promoted_at else None,
            "effective_from_chapter": p.effective_from_chapter,
        }
        for p in rows
    ]


class ProposalReviewIn(BaseModel):
    approve: bool
    reviewer: str | None = None


@router.post("/editorial/proposals/{proposal_id}/review")
async def review_proposal_endpoint(
    proposal_id: uuid.UUID,
    req: ProposalReviewIn,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.editorial.improvement import review_proposal

    try:
        p = await review_proposal(db, proposal_id, req.approve, req.reviewer)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return {"status": p.status}


class ExperimentIn(BaseModel):
    proposal_id: str | None = None
    case_ids: list[str] = Field(default_factory=list)
    use_gepa: bool = False


@router.post("/books/{book_id}/editorial/experiments", status_code=201)
async def create_experiment(
    book_id: uuid.UUID,
    req: ExperimentIn,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.editorial.improvement import run_experiment

    exp = await run_experiment(
        db,
        book_id,
        proposal_id=req.proposal_id,
        case_ids=req.case_ids or None,
        use_gepa=req.use_gepa,
    )
    if exp is None:
        raise HTTPException(status_code=409, detail="no active regression cases for this book")
    return {
        "id": str(exp.id),
        "status": exp.status,
        "recommendation": exp.recommendation,
        "metrics_baseline": exp.metrics_baseline,
        "metrics_candidate": exp.metrics_candidate,
        "hard_gate_results": exp.hard_gate_results,
        "pareto_candidates": exp.pareto_candidates or [],
    }


@router.get("/books/{book_id}/editorial/experiments")
async def list_experiments(
    book_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    from app.models.tables import EditorialExperiment

    rows = list(
        (
            await db.execute(
                select(EditorialExperiment)
                .where(EditorialExperiment.book_id == book_id)
                .order_by(EditorialExperiment.started_at.desc())
                .limit(50)
            )
        ).scalars()
    )
    return [
        {
            "id": str(e.id),
            "proposal_id": str(e.proposal_id) if e.proposal_id else None,
            "status": e.status,
            "recommendation": e.recommendation,
            "metrics_baseline": e.metrics_baseline,
            "metrics_candidate": e.metrics_candidate,
            "case_count": len(e.case_ids or []),
            "hard_pass": (e.hard_gate_results or {}).get("hard_pass"),
            "started_at": e.started_at.isoformat() if e.started_at else None,
        }
        for e in rows
    ]


class PromoteIn(BaseModel):
    effective_from_chapter: int | None = None


@router.post("/editorial/proposals/{proposal_id}/promote")
async def promote_proposal_endpoint(
    proposal_id: uuid.UUID,
    req: PromoteIn | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.editorial.improvement import promote_proposal

    try:
        p = await promote_proposal(
            db, proposal_id, req.effective_from_chapter if req else None
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return {"status": p.status, "effective_from_chapter": p.effective_from_chapter}


@router.post("/editorial/proposals/{proposal_id}/rollback")
async def rollback_proposal_endpoint(
    proposal_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.editorial.improvement import rollback_proposal

    try:
        p = await rollback_proposal(db, proposal_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if p is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return {"status": p.status}


@router.get("/books/{book_id}/editorial/metrics")
async def get_editorial_metrics(
    book_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.editorial.metrics import book_quality_metrics

    return await book_quality_metrics(db, book_id)


@router.get("/books/{book_id}/editorial/insights")
async def list_feedback_insights(
    book_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = list(
        (
            await db.execute(
                select(EditorialFeedbackInsight)
                .where(EditorialFeedbackInsight.book_id == book_id)
                .order_by(EditorialFeedbackInsight.created_at.desc())
                .limit(200)
            )
        ).scalars()
    )
    return [
        {
            "id": str(i.id),
            "annotation_id": str(i.annotation_id),
            "normalized_category": i.normalized_category,
            "human_intent": i.human_intent,
            "symptom": i.symptom,
            "root_cause_component": i.root_cause_component,
            "remediation_level": i.remediation_level,
            "confidence": i.confidence,
        }
        for i in rows
    ]
