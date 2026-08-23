"""v9.3 Editorial quality metrics (spec §59–§64, PR-07/PR-08).

Deterministic aggregation over submitted review rounds:

* First-pass yield — chapters accepted on round 1 / all reviewed chapters
* Score trend — (chapter_no, round_no, score, grade) series
* Category pareto — annotation/insight counts by normalized category
* Root-cause distribution — counts by attributed component
* AI-review agreement — confirmed / (confirmed + dismissed + corrected)
* AI escape rate — human critical/major issues AI never flagged
* Revision depth — chapters by number of rounds needed
* Experience card health — active / candidate counts
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import (
    Chapter,
    EditorialAnnotation,
    EditorialExperienceCard,
    EditorialFeedbackInsight,
    EditorialReviewRound,
)

ACCEPT_VERDICTS = {"accept", "accept_with_notes"}
BAD_GRADES = {"C", "D"}


async def book_quality_metrics(db: AsyncSession, book_id) -> dict:
    rounds = list(
        (
            await db.execute(
                select(EditorialReviewRound)
                .where(EditorialReviewRound.book_id == book_id)
                .order_by(EditorialReviewRound.submitted_at)
            )
        ).scalars()
    )
    submitted = [r for r in rounds if r.status == "submitted"]

    chapters = list(
        (
            await db.execute(
                select(Chapter)
                .where(Chapter.book_id == book_id)
                .order_by(Chapter.chapter_no)
            )
        ).scalars()
    )
    chapter_by_id = {c.id: c for c in chapters}

    anns = list(
        (
            await db.execute(
                select(EditorialAnnotation).where(EditorialAnnotation.book_id == book_id)
            )
        ).scalars()
    )
    insights = list(
        (
            await db.execute(
                select(EditorialFeedbackInsight).where(EditorialFeedbackInsight.book_id == book_id)
            )
        ).scalars()
    )
    cards = list(
        (
            await db.execute(
                select(EditorialExperienceCard).where(EditorialExperienceCard.book_id == book_id)
            )
        ).scalars()
    )

    # ── first-pass yield (spec §59) ──
    reviewed_chapters: dict = {}
    for r in submitted:
        reviewed_chapters.setdefault(r.chapter_id, []).append(r)
    total_reviewed = len(reviewed_chapters)
    first_pass = 0
    revision_depth: dict[str, int] = {}
    score_trend: list[dict] = []
    for ch_id, ch_rounds in reviewed_chapters.items():
        ch_rounds.sort(key=lambda r: r.round_no)
        rounds_needed = len(ch_rounds)
        revision_depth[str(min(rounds_needed, 3))] = revision_depth.get(str(min(rounds_needed, 3)), 0) + 1
        first = ch_rounds[0]
        if first.verdict in ACCEPT_VERDICTS:
            first_pass += 1
        last = ch_rounds[-1]
        ch = chapter_by_id.get(ch_id)
        score_trend.append(
            {
                "chapter_no": ch.chapter_no if ch else None,
                "round_no": last.round_no,
                "score": last.score_total,
                "grade": last.grade,
                "verdict": last.verdict,
            }
        )

    # ── category pareto (spec §61) ──
    pareto: dict[str, int] = {}
    for i in insights:
        pareto[i.normalized_category] = pareto.get(i.normalized_category, 0) + 1
    for a in anns:
        if a.annotation_type in {"question", "praise"}:
            continue
        if a.category:
            pareto[a.category] = pareto.get(a.category, 0) + 1

    # ── root-cause distribution (spec §62) ──
    root_causes: dict[str, int] = {}
    for i in insights:
        root_causes[i.root_cause_component] = root_causes.get(i.root_cause_component, 0) + 1

    # ── AI review calibration (spec §26–§28, PR-07) ──
    confirmed = dismissed = corrected = 0
    for r in submitted:
        for action in (r.ai_issue_dispositions or {}).values():
            if action == "confirmed":
                confirmed += 1
            elif action == "dismissed":
                dismissed += 1
            elif action == "corrected":
                corrected += 1
    total_dispositions = confirmed + dismissed + corrected

    # escape: human critical/major annotations AI never matched
    severe = [
        a for a in anns
        if a.severity in {"critical", "major"} and a.annotation_type not in {"praise", "question"}
    ]
    escaped = [a for a in severe if not (a.ai_issue_match_ids or [])]

    # ── status distribution ──
    status_dist: dict[str, int] = {}
    for c in chapters:
        status_dist[c.editorial_status or "pending_review"] = status_dist.get(c.editorial_status or "pending_review", 0) + 1

    # ── consecutive bad chapters (auto-pause signal) ──
    trend_sorted = sorted(score_trend, key=lambda t: t["chapter_no"] or 0)
    consecutive_bad = 0
    for t in reversed(trend_sorted):
        if t["grade"] in BAD_GRADES:
            consecutive_bad += 1
        else:
            break

    recent_scores = [t["score"] for t in trend_sorted[-5:] if t["score"] is not None]
    window_good_rate = (
        100.0 * sum(1 for s in recent_scores if s >= 85) / len(recent_scores) if recent_scores else None
    )

    return {
        "total_reviewed": total_reviewed,
        "first_pass_accepted": first_pass,
        "first_pass_yield": round(100.0 * first_pass / total_reviewed, 1) if total_reviewed else None,
        "score_trend": trend_sorted,
        "revision_depth": revision_depth,
        "category_pareto": dict(sorted(pareto.items(), key=lambda kv: -kv[1])),
        "root_causes": dict(sorted(root_causes.items(), key=lambda kv: -kv[1])),
        "ai_calibration": {
            "confirmed": confirmed,
            "dismissed": dismissed,
            "corrected": corrected,
            "agreement": round(100.0 * confirmed / total_dispositions, 1) if total_dispositions else None,
            "severe_human_issues": len(severe),
            "escaped": len(escaped),
            "escape_rate": round(100.0 * len(escaped) / len(severe), 1) if severe else None,
        },
        "status_distribution": status_dist,
        "consecutive_bad": consecutive_bad,
        "window_good_rate": window_good_rate,
        "experience_cards": {
            "active": sum(1 for c in cards if c.status == "active"),
            "candidate": sum(1 for c in cards if c.status == "candidate"),
            "locked": sum(1 for c in cards if c.status == "locked"),
            "rejected": sum(1 for c in cards if c.status == "rejected"),
        },
        "annotation_total": len(anns),
    }
