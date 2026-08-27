"""Model autopilot: per-role scoring (spec §11–§18, §53–§54, §58).

No global "smartness" score per model — one score per (model, agent_role).
Human-verdict data weighs in only once enough samples exist (spec §18).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.model_autopilot.seed import static_quality_score_for
from app.models import (
    Chapter,
    ChapterRun,
    EditorialAnnotation,
    EditorialFeedbackInsight,
    EditorialReviewRound,
    ModelCatalog,
    ModelHealthSnapshot,
    ModelRoleScore,
)

logger = logging.getLogger("novelforge.model_autopilot.scoring")

# root_cause_component → the role whose model score gets penalized (spec §54)
ROOT_CAUSE_ROLES = {
    "chapter_planner": "chapter_planner",
    "planner": "chapter_planner",
    "draft_writer": "draft_writer",
    "writer": "draft_writer",
    "review_agent": "review_agent",
    "reviewer": "review_agent",
    "state_extractor": "state_extractor",
    "style_analyzer": "style_analyzer",
}

DEFAULT_TIER_SCORE = 70.0


def _human_weight(sample_count: int) -> float:
    """Spec §18: sample-count based weight of human quality in composite."""
    if sample_count < 10:
        return 0.0
    if sample_count < 30:
        return 0.15
    if sample_count < 100:
        return 0.30
    return 0.40


async def _reliability(db: AsyncSession, catalog_id: uuid.UUID) -> tuple[float | None, dict]:
    snap = (
        await db.execute(
            select(ModelHealthSnapshot).where(
                ModelHealthSnapshot.model_catalog_id == catalog_id
            )
        )
    ).scalar_one_or_none()
    if snap is None or snap.success_rate_15m is None:
        return None, {}
    return round(min(1.0, snap.success_rate_15m) * 100, 1), {
        "health_status": snap.health_status,
        "health_score": snap.health_score,
    }


async def _production_quality(
    db: AsyncSession, catalog: ModelCatalog, agent_role: str
) -> tuple[float | None, float | None, int]:
    """Aggregate human verdict quality for chapters written by this model/role.

    Returns (production_quality_score, human_quality_score, sample_count).
    """
    runs = (
        (
            await db.execute(
                select(
                    ChapterRun.chapter_id,
                    ChapterRun.model_binding_snapshot,
                ).where(ChapterRun.writing_session_id.is_not(None))
            )
        )
        .all()
    )
    matched_chapter_ids = []
    for chapter_id, binding in runs:
        roles = (binding or {}).get("roles") or {}
        primary = (roles.get(agent_role) or {}).get("primary") or {}
        if primary.get("model") == catalog.model_id and primary.get("provider") == catalog.provider:
            matched_chapter_ids.append(chapter_id)
    if not matched_chapter_ids:
        return None, None, 0

    rounds = (
        (
            await db.execute(
                select(EditorialReviewRound).where(
                    EditorialReviewRound.chapter_id.in_(matched_chapter_ids),
                    EditorialReviewRound.round_no == 1,
                    EditorialReviewRound.status == "submitted",
                )
            )
        )
        .scalars()
        .all()
    )
    if not rounds:
        return None, None, 0

    blocking = set(
        (
            await db.execute(
                select(EditorialAnnotation.chapter_id).where(
                    EditorialAnnotation.chapter_id.in_(matched_chapter_ids),
                    EditorialAnnotation.is_blocking.is_(True),
                )
            )
        ).scalars().all()
    )
    good = sum(
        1
        for r in rounds
        if r.verdict == "accept" or (r.verdict == "accept_with_notes" and r.chapter_id not in blocking)
    )
    rate = good / len(rounds)

    # spec §54: human root-cause attribution hits the responsible role's model.
    # Attribution is per-chapter; each insight for THIS role costs 2 points
    # (capped at 10), and no other role's score is touched.
    insights = (
        (
            await db.execute(
                select(EditorialFeedbackInsight).where(
                    EditorialFeedbackInsight.chapter_id.in_(matched_chapter_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    attributed = [
        i for i in insights
        if ROOT_CAUSE_ROLES.get(i.root_cause_component) == agent_role
    ]
    penalty = min(10.0, 2.0 * len(attributed))
    score = max(0.0, rate * 100 - penalty)
    return round(score, 1), round(score, 1), len(rounds)


async def compute_role_score(
    db: AsyncSession, catalog: ModelCatalog, agent_role: str
) -> ModelRoleScore:
    """Compute and upsert one (model, role) score row."""
    now = datetime.now(timezone.utc)
    row = (
        await db.execute(
            select(ModelRoleScore).where(
                ModelRoleScore.model_catalog_id == catalog.id,
                ModelRoleScore.agent_role == agent_role,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ModelRoleScore(
            id=uuid.uuid4(),
            model_catalog_id=catalog.id,
            agent_role=agent_role,
            score_version="v1",
        )
        db.add(row)
        await db.flush()

    row.static_prior_score = static_quality_score_for(catalog.model_id) or DEFAULT_TIER_SCORE
    reliability, health_detail = await _reliability(db, catalog.id)
    prod_score, human_score, sample_count = await _production_quality(db, catalog, agent_role)

    row.reliability_score = reliability
    row.human_quality_score = human_score
    row.production_quality_score = prod_score
    row.sample_count = sample_count
    # v9.8: KEEP the benchmark (qualification) score — it is real ability
    # evidence, NOT to be discarded. It is folded into the composite below.
    # row.benchmark_score = None  # (removed — was discarding real signal, P0-2)

    human_w = _human_weight(sample_count)
    base = prod_score if (prod_score is not None and sample_count >= 10) else (row.static_prior_score or DEFAULT_TIER_SCORE)
    composite = round(human_w * (human_score or base) + (1 - human_w) * base, 1)
    # v9.8 (P0-4): the one-time qualification benchmark score reaches routing,
    # BUT only when its evidence key still matches the model's CURRENT valid
    # ability key. After an identity/suite/evaluator change the old
    # benchmark_score must NOT keep contributing to the composite.
    benchmark_blended = None
    benchmark_state = None
    if row.benchmark_score is not None:
        from app.model_eval.engine import get_catalog_evidence_state

        evidence = await get_catalog_evidence_state(db, catalog)
        role_evidence = (evidence.get("role_evidence") or {}).get(agent_role) or {}
        benchmark_state = {
            "ability": (evidence.get("ability") or {}).get("state"),
            "role": role_evidence.get("state"),
            "passed": role_evidence.get("passed"),
        }
        if (
            evidence.get("ability", {}).get("state") == "valid"
            and role_evidence.get("state") == "valid"
            and row.benchmark_evidence_key == evidence.get("ability_evaluation_key")
        ):
            composite = round(0.25 * row.benchmark_score + 0.75 * composite, 1)
            benchmark_blended = True
        else:
            benchmark_blended = False
    if reliability is not None:
        # reliability blends into composite lightly (±10%)
        composite = round(0.9 * composite + 0.1 * reliability, 1)
    row.composite_score = composite
    row.confidence = min(1.0, (sample_count / 100) * 0.5 + 0.5)
    row.detail_json = {
        "human_weight": human_w,
        "health": health_detail,
        "reliability": reliability,
        "production_quality": prod_score,
        "human_quality": human_score,
        "benchmark_score": row.benchmark_score,
        "benchmark_blended": benchmark_blended,
        "benchmark_state": benchmark_state,
        "sample_count": sample_count,
        "computed_at": now.isoformat(),
    }
    return row
