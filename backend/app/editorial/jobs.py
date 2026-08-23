"""v9.3 Editorial Learning Loop worker jobs (PR-03 / PR-05).

Two arq jobs:

* ``run_editorial_revision_job`` — execute the L0..L5 remediation ladder:
  L0 learning-only, L1 deterministic patch (+ optional LLM polish),
  L2-L4 constrained LLM rewrite, L5 improvement proposal. Every new
  version carries lineage (parent_version_id / review_round / origin)
  and annotations are re-anchored onto the new text (fail-open: unmatched
  annotations become ``moved`` so the human sees what drifted).
* ``analyze_editorial_review_job`` — deterministic feedback analysis:
  root-cause attribution per annotation, preference pairs from direct
  edits, regression case snapshot, first-pass-yield bookkeeping.

Both jobs are idempotent-ish: they re-read state from the DB and never
assume the queue delivered them exactly once.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.editorial.anchoring import relocate, split_paragraphs
from app.models.tables import (
    Chapter,
    ChapterVersion,
    EditorialAnnotation,
    EditorialFeedbackInsight,
    EditorialImprovementProposal,
    EditorialPreferencePair,
    EditorialRegressionCase,
    EditorialReviewRound,
)

logger = logging.getLogger("novelforge.editorial_jobs")

# annotation category → root-cause component (deterministic attribution table)
CATEGORY_COMPONENT = {
    "plot": "chapter_planner",
    "structure": "chapter_planner",
    "goal_drift": "chapter_planner",
    "causality": "ccne",
    "continuity": "memory",
    "consistency": "memory",
    "worldbuilding": "memory",
    "character": "voice",
    "voice": "voice",
    "dialogue": "voice",
    "style": "style",
    "prose": "style",
    "pacing": "style",
    "description": "style",
    "immersion": "style",
    "logic": "ccne",
    "contract_violation": "ccne",
    "review_miss": "review_agent",
    "patch_error": "patch_editor",
    "other": "draft_writer",
}

SCOPE_COMPONENT_HINT = {
    "scene": "chapter_planner",
    "scene_type": "chapter_planner",
    "character": "voice",
    "book_future": "chapter_planner",
    "global": "style",
}

SEVERITY_LEVEL = {"critical": 4, "major": 3, "minor": 2, "note": 1, "praise": 0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── L1 deterministic patch ────────────────────────────────────────────


def apply_direct_edits(
    paragraphs: list[str], annotations: list[EditorialAnnotation]
) -> tuple[list[str], int, set]:
    """Apply direct_edit suggested_text onto the paragraph list.

    Returns (new_paragraphs, applied_count, applied_ids). Edits whose quote
    no longer matches are skipped (the LLM/fallback writer handles them, or
    they stay for the human to re-apply in recheck).
    """
    result = list(paragraphs)
    applied = 0
    applied_ids: set = set()
    edits = [a for a in annotations if a.annotation_type == "direct_edit" and a.suggested_text]
    # apply bottom-up (highest paragraph & offset first) so earlier spans stay valid
    for a in sorted(
        edits,
        key=lambda x: (int(x.paragraph_key or 0), x.start_offset or 0),
        reverse=True,
    ):
        try:
            key = int(a.paragraph_key)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if not (0 <= key < len(result)):
            continue
        para = result[key]
        quote = a.quoted_text or ""
        if not quote:
            continue
        pos = para.find(quote)
        if pos < 0:
            continue
        result[key] = para[:pos] + (a.suggested_text or "") + para[pos + len(quote) :]
        applied += 1
        applied_ids.add(a.id)
    return result, applied, applied_ids


def build_revision_instructions(annotations: list[EditorialAnnotation], level: str) -> str:
    """Render annotations as numbered, anchored instructions for the LLM."""
    lines: list[str] = []
    for i, a in enumerate(annotations, 1):
        if a.annotation_type == "praise":
            continue
        loc = f"第{a.paragraph_key}段" if a.paragraph_key is not None else a.scope
        quote = f"「{(a.quoted_text or '')[:80]}」" if a.quoted_text else ""
        fix = f"改为「{(a.suggested_text or '')[:120]}」" if a.suggested_text else ""
        comment = a.comment or ""
        lines.append(f"{i}. [{loc}]{quote} {comment} {fix}".strip())
    header = {
        "L1": "逐条精确修复以下批注，不得改动批注以外的文字：",
        "L2": "重写涉及的场景以解决以下批注，其余段落保持原样：",
        "L3": "在保持大纲与章节目标不变的前提下整章重写，必须解决以下批注：",
        "L4": "重新规划场景结构并重写全章，必须解决以下批注：",
    }.get(level, "解决以下批注：")
    return header + "\n" + "\n".join(lines) if lines else header + "\n（无文字批注）"


# ── LLM rewrite (optional; graceful fallback) ─────────────────────────


def _revision_model() -> str:
    import os

    return os.environ.get("EDITORIAL_REVISION_MODEL", "deepseek-chat")


async def _llm_rewrite(content: str, instructions: str) -> str | None:
    try:
        from app.gateway.model_gateway import stream_completion_and_collect

        system = (
            "你是小说修订编辑。严格按修订指令改写章节正文。"
            "输出仅包含修订后的完整章节正文（按空行分段），不要任何解释、标题或标记。"
        )
        user = f"<修订指令>\n{instructions}\n</修订指令>\n\n<当前正文>\n{content}\n</当前正文>"
        result = await stream_completion_and_collect(
            system_prompt=system,
            user_content=user,
            model=_revision_model(),
            temperature=0.4,
            max_tokens=16384,
        )
        text = (result.text or "").strip()
        return text if len(text) > len(content) * 0.3 else None
    except Exception as e:  # noqa: BLE001 - LLM unavailability must not break the loop
        logger.warning("LLM rewrite unavailable: %s", e)
        return None


# ── version + re-anchor helpers ───────────────────────────────────────


async def _latest_version(db: AsyncSession, chapter_id) -> ChapterVersion | None:
    return (
        await db.execute(
            select(ChapterVersion)
            .where(ChapterVersion.chapter_id == chapter_id)
            .order_by(ChapterVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _create_revision_version(
    db: AsyncSession,
    round_: EditorialReviewRound,
    base: ChapterVersion,
    new_content: str,
    origin: str,
    applied_ids: set | None = None,
) -> ChapterVersion:
    version = ChapterVersion(
        book_id=round_.book_id,
        chapter_id=round_.chapter_id,
        version=base.version + 1,
        content=new_content,
        word_count=len(new_content),
        source_run_id=base.source_run_id,
        version_kind="editorial_revision",
        content_hash=_sha(new_content),
        parent_version_id=base.id,
        editorial_review_round_id=round_.id,
        revision_origin=origin,
    )
    db.add(version)
    await db.flush()

    # re-anchor this round's annotations onto the new text (fail-open)
    new_paras = split_paragraphs(new_content)
    for ann in (
        await db.execute(
            select(EditorialAnnotation).where(EditorialAnnotation.review_round_id == round_.id)
        )
    ).scalars():
        if ann.paragraph_key is None or ann.annotation_type == "praise":
            continue
        if applied_ids and ann.id in applied_ids:
            # the edit itself is now part of the new text — quote is gone by design
            ann.resolution_status = "resolved"
            ann.resolved_by_version_id = version.id
            continue
        try:
            r = relocate(
                new_paras,
                {
                    "paragraph_key": ann.paragraph_key,
                    "quoted_text": ann.quoted_text or "",
                    "context_before": ann.context_before or "",
                    "context_after": ann.context_after or "",
                },
            )
            if r.paragraph_key is not None:
                ann.paragraph_key = str(r.paragraph_key)
                ann.start_offset = r.start_offset
                ann.end_offset = r.end_offset
            if r.resolution_status == "open" and ann.annotation_type == "direct_edit":
                ann.resolution_status = "resolved"
                ann.resolved_by_version_id = version.id
            elif r.resolution_status != "open":
                ann.resolution_status = "moved"
        except Exception:  # noqa: BLE001 - one bad anchor must not abort the rest
            ann.resolution_status = "moved"
    return version


# ── job: revision ladder ──────────────────────────────────────────────


async def run_editorial_revision_job(ctx, review_id: str, level: str | None = None) -> dict:
    async with async_session_factory() as db:
        rnd = (
            await db.execute(
                select(EditorialReviewRound).where(EditorialReviewRound.id == review_id)
            )
        ).scalar_one_or_none()
        if rnd is None:
            logger.error("revision job: round %s not found", review_id)
            return {"status": "not_found"}

        chapter = (
            await db.execute(select(Chapter).where(Chapter.id == rnd.chapter_id))
        ).scalar_one_or_none()
        if chapter is None:
            return {"status": "chapter_missing"}

        base = await _latest_version(db, rnd.chapter_id)
        if base is None:
            chapter.editorial_status = "revision_requested"
            await db.commit()
            return {"status": "no_version"}

        annotations = list(
            (
                await db.execute(
                    select(EditorialAnnotation).where(EditorialAnnotation.review_round_id == rnd.id)
                )
            ).scalars()
        )
        lv = level or "L1"

        # ── L0: learning only — no regeneration, chapter waived ──
        if lv == "L0":
            chapter.editorial_status = "waived"
            await db.commit()
            return {"status": "waived_learning_only"}

        # ── L5: system improvement proposal, chapter stays as-is ──
        if lv == "L5":
            blocking = [a for a in annotations if a.annotation_type != "praise"]
            categories = sorted({a.category or "other" for a in blocking}) or ["other"]
            proposal = EditorialImprovementProposal(
                book_id=rnd.book_id,
                proposal_type="editorial_feedback_batch",
                target_component=CATEGORY_COMPONENT.get(categories[0], "draft_writer"),
                target_scope="book",
                candidate_patch={
                    "remediation_level": "L5",
                    "categories": categories,
                    "instruction_count": len(blocking),
                    "summary": rnd.overall_comment or "",
                },
                risk_level="low",
                reason=f"第{rnd.round_no}轮人工审核触发 L5：{len(blocking)} 条批注建议系统性改进",
                supporting_review_ids=[str(rnd.id)],
            )
            db.add(proposal)
            chapter.editorial_status = "waived"
            await db.commit()
            return {"status": "proposal_created"}

        # ── L1..L4: produce a revised version ──
        paragraphs = split_paragraphs(base.content or "")

        if lv == "L1":
            new_paras, applied, applied_ids = apply_direct_edits(paragraphs, annotations)
            llm_edits = [a for a in annotations if a.annotation_type != "direct_edit" and a.annotation_type != "praise"]
            if llm_edits:
                instructions = build_revision_instructions(llm_edits, "L1")
                rewritten = await _llm_rewrite("\n\n".join(new_paras), instructions)
                if rewritten:
                    new_paras = split_paragraphs(rewritten)
            new_content = "\n\n".join(new_paras)
        else:
            instructions = build_revision_instructions(annotations, lv)
            rewritten = await _llm_rewrite(base.content or "", instructions)
            if rewritten is None:
                # graceful fallback: deterministic patch only, flagged via origin
                new_paras, _, applied_ids = apply_direct_edits(paragraphs, annotations)
                new_content = "\n\n".join(new_paras)
                origin = "editorial_revision_fallback"
                version = await _create_revision_version(db, rnd, base, new_content, origin, applied_ids)
                chapter.editorial_status = "awaiting_recheck"
                await db.commit()
                return {"status": "fallback_patched", "version": version.version}
            new_content = rewritten
            applied_ids = set()

        origin = "editorial_revision" if lv != "L4" else "editorial_replan"
        version = await _create_revision_version(db, rnd, base, new_content, origin, applied_ids)
        chapter.editorial_status = "awaiting_recheck"
        await db.commit()
        logger.info(
            "editorial revision round %s level %s → version %s",
            review_id,
            lv,
            version.version,
        )
        return {"status": "revised", "version": version.version, "level": lv}


# ── job: deterministic feedback analysis ──────────────────────────────


async def analyze_editorial_review_job(ctx, review_id: str) -> dict:
    async with async_session_factory() as db:
        rnd = (
            await db.execute(
                select(EditorialReviewRound).where(EditorialReviewRound.id == review_id)
            )
        ).scalar_one_or_none()
        if rnd is None or rnd.status != "submitted":
            return {"status": "skipped"}

        annotations = list(
            (
                await db.execute(
                    select(EditorialAnnotation).where(EditorialAnnotation.review_round_id == rnd.id)
                )
            ).scalars()
        )
        version = (
            await db.execute(
                select(ChapterVersion).where(ChapterVersion.id == rnd.chapter_version_id)
            )
        ).scalar_one_or_none()

        insights = 0
        pairs = 0
        for a in annotations:
            category = a.category or "other"
            if a.category is None:
                component = SCOPE_COMPONENT_HINT.get(a.scope, "draft_writer")
            else:
                component = CATEGORY_COMPONENT.get(
                    category, SCOPE_COMPONENT_HINT.get(a.scope, "draft_writer")
                )

            existing = (
                await db.execute(
                    select(EditorialFeedbackInsight).where(
                        EditorialFeedbackInsight.annotation_id == a.id
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                db.add(
                    EditorialFeedbackInsight(
                        annotation_id=a.id,
                        book_id=rnd.book_id,
                        normalized_category=category,
                        human_intent=a.comment,
                        symptom=(a.quoted_text or "")[:500] or None,
                        root_cause_component=component,
                        secondary_components=[],
                        remediation_level="L1" if a.annotation_type != "direct_edit" else "L0",
                        confidence=0.7 if a.annotation_type != "question" else 0.4,
                        evidence_refs=[str(rnd.id)],
                    )
                )
                insights += 1

            # direct edits also become preference pairs (spec §17)
            if a.annotation_type == "direct_edit" and a.suggested_text and a.quoted_text:
                dupe = (
                    await db.execute(
                        select(EditorialPreferencePair).where(
                            EditorialPreferencePair.annotation_id == a.id
                        )
                    )
                ).scalar_one_or_none()
                if dupe is None:
                    db.add(
                        EditorialPreferencePair(
                            book_id=rnd.book_id,
                            chapter_id=rnd.chapter_id,
                            review_round_id=rnd.id,
                            annotation_id=a.id,
                            rejected_text=a.quoted_text,
                            chosen_text=a.suggested_text,
                            preference_reason=a.comment,
                            category=category,
                            scope=a.scope,
                            source="human_direct_edit",
                        )
                    )
                    pairs += 1

        # regression case snapshot for every submitted round (spec §48)
        if version is not None:
            snap = (
                await db.execute(
                    select(EditorialRegressionCase).where(
                        EditorialRegressionCase.source_review_round_id == rnd.id
                    )
                )
            ).scalar_one_or_none()
            if snap is None:
                db.add(
                    EditorialRegressionCase(
                        book_id=rnd.book_id,
                        source_review_round_id=rnd.id,
                        chapter_version_id=version.id,
                        case_type="chapter_review",
                        target_component="review_agent",
                        chapter_text=version.content or "",
                        human_verdict=rnd.verdict,
                        rubric_scores=rnd.rubric_scores_json,
                        human_annotation_ids=[a.id for a in annotations],
                        expected_properties=[],
                        forbidden_properties=[
                            {"category": a.category, "quote": a.quoted_text}
                            for a in annotations
                            if a.annotation_type == "forbidden_pattern"
                        ],
                        scene_type=None,
                    )
                )

        await db.commit()

        # aggregate this round into experience cards (merge/dedupe, spec §33)
        from app.editorial.experience import synthesize_cards_from_review

        synth = await synthesize_cards_from_review(db, review_id)
        logger.info(
            "analyzed review %s: %d insights, %d preference pairs, %s cards",
            review_id,
            insights,
            pairs,
            {k: synth.get(k) for k in ("created", "merged", "auto_activated")},
        )
        return {
            "status": "ok",
            "insights": insights,
            "preference_pairs": pairs,
            "cards_created": synth.get("created", 0),
            "cards_merged": synth.get("merged", 0),
        }
