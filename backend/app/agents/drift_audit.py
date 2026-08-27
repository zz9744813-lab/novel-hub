"""DriftAuditAgent - per 30 chapters, audits state card accuracy, retrieval, outline adherence.
Per §9 + §A.7 v7.3.
"""
import uuid
import json
import hashlib
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.agents.caller import call_agent
from app.models import (
    Chapter, ChapterVersion, DriftAuditReport, MemoryL4StateSnapshot, StoryEvent,
    OutlineNode, StyleVoiceCard, StyleToneAnchor
)

logger = logging.getLogger("novelforge.drift_audit")


async def _causal_metrics(
    db,
    book_id: uuid.UUID,
    chapter_start: int,
    chapter_end: int,
) -> dict:
    """Deterministic v9 causal metrics over the chapter range (spec §34)."""
    from app.models import Chapter, SceneReasoningContract, StoryEventEdge
    from sqlalchemy import func

    try:
        chapter_ids = (
            await db.execute(
                select(Chapter.id).where(
                    Chapter.book_id == book_id,
                    Chapter.chapter_no >= chapter_start,
                    Chapter.chapter_no <= chapter_end,
                )
            )
        ).scalars().all()
        if not chapter_ids:
            return {}

        contracts = (
            await db.execute(
                select(SceneReasoningContract).where(
                    SceneReasoningContract.chapter_id.in_(list(chapter_ids))
                )
            )
        ).scalars().all()
        edge_count = (
            await db.execute(
                select(func.count()).select_from(StoryEventEdge).where(
                    StoryEventEdge.chapter_id.in_(list(chapter_ids))
                )
            )
        ).scalar() or 0

        total = len(contracts)
        finalized = sum(1 for c in contracts if c.status == "finalized")

        from app.engine.counterfactual_audit import audit_counterfactual

        contract_dicts = [c.contract_json for c in contracts if isinstance(c.contract_json, dict)]
        cf = audit_counterfactual(contract_dicts, {})
        necessary = sum(
            1 for f in cf.findings if f.classification == "necessary_support"
        )
        redundancy = sum(
            1 for f in cf.findings if f.classification in ("motivation_redundancy", "false_causal_emphasis")
        )

        return {
            "causal_contract_total": total,
            "causal_contract_finalized": finalized,
            "causal_contract_realization_rate": (finalized / total) if total else None,
            "causal_edge_count": int(edge_count),
            "necessary_support_count": necessary,
            "motivation_redundancy_count": redundancy,
        }
    except Exception as e:
        logger.warning("causal metrics computation failed: %s", e)
        return {}


# Thresholds per §9.3
THRESHOLDS = {
    "state_card_accuracy": {"green": 0.985, "yellow": 0.970, "red": 0.970},
    "retrieval_recall_at_8": {"green": 0.930, "yellow": 0.900, "red": 0.900},
    "retrieval_precision_at_8": {"green": 0.700, "yellow": 0.550},
    "required_fact_injection_rate": {"green": 1.000, "red": 1.000},
    "outline_adherence": {"green": 0.950, "yellow": 0.920, "red": 0.920},
    "character_voice_consistency": {"green": 0.900, "yellow": 0.800},
    "narrative_tone_anchor_score": {"green": 0.900, "yellow": 0.800},
}

# Redline conditions per §9.3
REDLINE_TYPES = [
    "character_death_error",
    "core_identity_error",
    "core_relationship_reversed",
    "ability_breakthrough_unexplained",
    "item_held_by_multiple",
    "timeline_contradiction",
    "required_dependency_bypassed",
    "irreversible_forbidden_outcome",
]

_STATUS_RANK = {"green": 0, "yellow": 1, "red": 2}


def classify_drift_status(
    metrics: dict,
    *,
    requested_status: str,
    redline_findings: list | None = None,
) -> str:
    """Apply configured thresholds instead of trusting a model's colour label."""

    requested = str(requested_status or "").lower()
    if requested not in _STATUS_RANK:
        raise ValueError("drift status must be green, yellow, or red")
    if not isinstance(metrics, dict):
        raise ValueError("drift metrics must be an object")
    missing = sorted(set(THRESHOLDS) - set(metrics))
    if missing:
        raise ValueError(f"missing drift metrics: {missing}")

    status = requested
    for name, thresholds in THRESHOLDS.items():
        value = metrics.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"drift metric {name} must be numeric")
        score = float(value)
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"drift metric {name} must be within 0..1")
        measured = "green"
        red_floor = thresholds.get("red")
        if red_floor is not None and score < float(red_floor):
            measured = "red"
        elif score < float(thresholds["green"]):
            measured = "yellow"
        if _STATUS_RANK[measured] > _STATUS_RANK[status]:
            status = measured
    if redline_findings:
        status = "red"
    return status


def stratified_chapter_numbers(start: int, end: int, count: int = 6) -> list[int]:
    """Select deterministic, evenly spread chapters including both boundaries."""
    if start < 1 or end < start or count < 1:
        raise ValueError("invalid chapter sampling range")
    total = end - start + 1
    if total <= count:
        return list(range(start, end + 1))
    if count == 1:
        return [start]
    values = {
        start + round(index * (total - 1) / (count - 1))
        for index in range(count)
    }
    return sorted(values)


def _chapter_excerpt(content: str, max_chars: int = 2400) -> str:
    text = content or ""
    if len(text) <= max_chars:
        return text
    third = max_chars // 3
    middle = max(0, len(text) // 2 - third // 2)
    return "\n[…中段…]\n".join(
        (text[:third], text[middle : middle + third], text[-third:])
    )


async def run_drift_audit(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_range_start: int,
    chapter_range_end: int,
) -> DriftAuditReport:
    """Run a drift audit for chapters [start, end]."""
    # Get events in range
    events = await db.execute(
        select(StoryEvent)
        .join(Chapter, Chapter.id == StoryEvent.chapter_id)
        .where(
            StoryEvent.book_id == book_id,
            Chapter.chapter_no >= chapter_range_start,
            Chapter.chapter_no <= chapter_range_end,
        )
        .order_by(Chapter.chapter_no, StoryEvent.created_at)
        .limit(200)
    )
    event_list = [{"id": str(e.id), "type": e.event_type, "certainty": e.certainty,
                    "excerpt": e.evidence_excerpt[:200]} for e in events.scalars().all()]

    # Get outline nodes in range
    nodes = await db.execute(
        select(OutlineNode)
        .join(Chapter, Chapter.outline_node_id == OutlineNode.id)
        .where(
            OutlineNode.book_id == book_id,
            OutlineNode.chapter_no >= chapter_range_start,
            OutlineNode.chapter_no <= chapter_range_end,
        )
        .order_by(OutlineNode.chapter_no)
    )
    node_list = [{"chapter_no": n.chapter_no, "goal": n.goal,
                   "required_beats": n.required_beats, "depends_on": n.depends_on} for n in nodes.scalars().all()]

    # Get L4 states
    l4 = await db.execute(
        select(MemoryL4StateSnapshot)
        .where(
            MemoryL4StateSnapshot.book_id == book_id,
            MemoryL4StateSnapshot.as_of_chapter <= chapter_range_end,
        )
        .order_by(
            MemoryL4StateSnapshot.entity_type,
            MemoryL4StateSnapshot.entity_id,
            MemoryL4StateSnapshot.as_of_chapter.desc(),
            MemoryL4StateSnapshot.version.desc(),
        )
    )
    latest_l4 = {}
    for state in l4.scalars().all():
        latest_l4.setdefault((state.entity_type, state.entity_id), state)
    l4_states = [
        {
            "entity_type": state.entity_type,
            "entity_id": str(state.entity_id),
            "as_of_chapter": state.as_of_chapter,
            "state": state.state,
        }
        for state in latest_l4.values()
    ]

    # Get voice cards
    vc = await db.execute(
        select(StyleVoiceCard)
        .where(StyleVoiceCard.book_id == book_id)
        .order_by(StyleVoiceCard.version.desc())
    )
    latest_voices = {}
    for voice in vc.scalars().all():
        latest_voices.setdefault(voice.character_id, voice)
    voice_cards = [
        {"character_id": str(v.character_id), "register": v.register}
        for v in latest_voices.values()
    ]

    # Get tone anchors
    ta = await db.execute(
        select(StyleToneAnchor)
        .where(StyleToneAnchor.book_id == book_id)
        .order_by(StyleToneAnchor.version.desc())
        .limit(1)
    )
    tone_anchors = [{"pov": t.narrative_pov} for t in ta.scalars().all()]

    sample_numbers = stratified_chapter_numbers(
        chapter_range_start, chapter_range_end
    )
    sample_rows = (
        await db.execute(
            select(
                Chapter.chapter_no,
                ChapterVersion.content,
                ChapterVersion.content_hash,
            )
            .join(
                ChapterVersion,
                (ChapterVersion.chapter_id == Chapter.id)
                & (ChapterVersion.version == Chapter.finalized_version),
            )
            .where(
                Chapter.book_id == book_id,
                Chapter.chapter_no.in_(sample_numbers),
                Chapter.status == "finalized",
                ChapterVersion.version_kind == "final",
            )
            .order_by(Chapter.chapter_no)
        )
    ).all()
    drift_samples = [
        {
            "sample_id": f"chapter-{chapter_no}",
            "chapter_no": chapter_no,
            "content_hash": content_hash,
            "excerpt": _chapter_excerpt(content),
        }
        for chapter_no, content, content_hash in sample_rows
    ]

    # v9 deterministic causal metrics (no LLM — authoritative, spec §34)
    causal_metrics = await _causal_metrics(db, book_id, chapter_range_start, chapter_range_end)

    audit_payload = {
        "chapter_range": [chapter_range_start, chapter_range_end],
        "audit_samples": [
            {
                "sample_id": item["sample_id"],
                "chapter_no": item["chapter_no"],
                "content_hash": item["content_hash"],
            }
            for item in drift_samples
        ],
        "l4_state": l4_states,
        "story_events": event_list,
        "outline_nodes": node_list,
        "voice_cards": voice_cards,
        "tone_anchors": tone_anchors,
        "drift_samples": drift_samples,
        "causal_metrics": causal_metrics,
    }
    input_hash = hashlib.sha256(
        json.dumps(
            audit_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    existing = (
        await db.execute(
            select(DriftAuditReport)
            .where(
                DriftAuditReport.book_id == book_id,
                DriftAuditReport.chapter_range_start == chapter_range_start,
                DriftAuditReport.chapter_range_end == chapter_range_end,
            )
            .order_by(DriftAuditReport.created_at.desc(), DriftAuditReport.id.desc())
        )
    ).scalars().all()
    for prior in existing:
        refs = prior.evidence_refs or []
        same_input = any(
            isinstance(ref, dict)
            and ref.get("kind") == "audit_input_hash"
            and ref.get("sha256") == input_hash
            for ref in refs
        )
        service_failed = any(
            isinstance(item, dict) and item.get("type") == "audit_service_failure"
            for item in (prior.yellow_findings or [])
        )
        if same_input and not service_failed:
            return prior

    user_content = json.dumps(audit_payload, ensure_ascii=False)

    # Release the read transaction before the model call.
    await db.commit()

    run, result, meta = await call_agent(
        book_id=book_id,
        agent_role="drift_audit",
        user_content=user_content,
    )
    meta = meta or {}

    # Create report
    report = DriftAuditReport(
        id=uuid.uuid4(),
        book_id=book_id,
        chapter_range_start=chapter_range_start,
        chapter_range_end=chapter_range_end,
        status="green",
        metrics={},
        redline_findings=[],
        yellow_findings=[],
        affected_entities=[],
        affected_future_nodes=[],
        recommended_actions=[],
        evidence_refs=[
            {"kind": "audit_input_hash", "sha256": input_hash},
            {
                "kind": "chapter_samples",
                "chapters": [
                    {
                        "chapter_no": item["chapter_no"],
                        "content_hash": item["content_hash"],
                    }
                    for item in drift_samples
                ],
            },
        ],
    )

    service_error = (
        not isinstance(result, dict)
        or bool(meta.get("error"))
        or bool(meta.get("block_reason"))
    )
    if not service_error:
        report.metrics = result.get("metrics", {})
        report.redline_findings = result.get("redline_findings", [])
        try:
            report.status = classify_drift_status(
                report.metrics,
                requested_status=result.get("status"),
                redline_findings=report.redline_findings,
            )
        except ValueError as exc:
            service_error = True
            meta = {**meta, "block_reason": str(exc)}
        report.yellow_findings = result.get("yellow_findings", [])
        report.affected_entities = result.get("affected_entities", [])
        report.affected_future_nodes = result.get("affected_future_nodes", [])
        report.recommended_actions = result.get("recommended_actions", [])
    if service_error:
        report.status = "yellow"
        report.yellow_findings = [
            {
                "type": "audit_service_failure",
                "detail": meta.get("block_reason") or meta.get("error") or "invalid audit response",
            }
        ]

    # Deterministic causal metrics override LLM-fuzzy values
    if causal_metrics:
        metrics = dict(report.metrics or {})
        metrics.update(causal_metrics)
        report.metrics = metrics
        necessary = int(causal_metrics.get("necessary_support_count") or 0)
        redundancy = int(causal_metrics.get("motivation_redundancy_count") or 0)
        if redundancy > 0 and report.status == "green":
            report.status = "yellow"
        if necessary > 0:
            report.yellow_findings = list(report.yellow_findings or []) + [
                {
                    "type": "causal_necessary_support",
                    "detail": f"{necessary} 个关键事件为唯一支持路径，脆弱度高",
                }
            ]

    db.add(report)
    await db.flush()

    # v9.7 §19: RED is enforced deterministically — future writing is blocked
    # until a human fixes canon/outline; the session controller reads this row.
    if report.status == "red":
        logger.warning(
            "DriftAudit RED for chapters %s-%s; affected=%s — session will block",
            chapter_range_start, chapter_range_end,
            (report.affected_future_nodes or [])[:5],
        )
    elif report.status == "yellow":
        logger.info(f"DriftAudit YELLOW for chapters {chapter_range_start}-{chapter_range_end}")
    else:
        logger.info(f"DriftAudit GREEN for chapters {chapter_range_start}-{chapter_range_end}")

    return report
