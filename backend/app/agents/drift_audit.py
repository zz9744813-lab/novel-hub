"""DriftAuditAgent - per 30 chapters, audits state card accuracy, retrieval, outline adherence.
Per §9 + §A.7 v7.3.
"""
import uuid
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.agents.caller import call_agent
from app.models import (
    DriftAuditReport, MemoryL4StateSnapshot, StoryEvent,
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


async def run_drift_audit(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_range_start: int,
    chapter_range_end: int,
) -> DriftAuditReport:
    """Run a drift audit for chapters [start, end]."""
    
    # Get events in range
    events = await db.execute(
        select(StoryEvent).where(
            StoryEvent.book_id == book_id,
        ).order_by(StoryEvent.created_at.desc()).limit(100)
    )
    event_list = [{"id": str(e.id), "type": e.event_type, "certainty": e.certainty,
                    "excerpt": e.evidence_excerpt[:200]} for e in events.scalars().all()]

    # Get outline nodes in range
    nodes = await db.execute(
        select(OutlineNode).where(
            OutlineNode.book_id == book_id,
            OutlineNode.chapter_no >= chapter_range_start,
            OutlineNode.chapter_no <= chapter_range_end,
        ).order_by(OutlineNode.chapter_no)
    )
    node_list = [{"chapter_no": n.chapter_no, "goal": n.goal,
                   "required_beats": n.required_beats, "depends_on": n.depends_on} for n in nodes.scalars().all()]

    # Get L4 states
    l4 = await db.execute(
        select(MemoryL4StateSnapshot).where(MemoryL4StateSnapshot.book_id == book_id)
    )
    l4_states = [{"entity_type": s.entity_type, "state": s.state} for s in l4.scalars().all()]

    # Get voice cards
    vc = await db.execute(select(StyleVoiceCard).where(StyleVoiceCard.book_id == book_id))
    voice_cards = [{"register": v.register} for v in vc.scalars().all()]

    # Get tone anchors
    ta = await db.execute(select(StyleToneAnchor).where(StyleToneAnchor.book_id == book_id))
    tone_anchors = [{"pov": t.narrative_pov} for t in ta.scalars().all()]

    # v9 deterministic causal metrics (no LLM — authoritative, spec §34)
    causal_metrics = await _causal_metrics(db, book_id, chapter_range_start, chapter_range_end)

    user_content = json.dumps({
        "chapter_range": [chapter_range_start, chapter_range_end],
        "audit_samples": [],  # TODO: generate proper audit samples
        "l4_state": l4_states,
        "story_events": event_list,
        "outline_nodes": node_list,
        "voice_cards": voice_cards,
        "tone_anchors": tone_anchors,
        "drift_samples": [],
    }, ensure_ascii=False)

    run, result, meta = await call_agent(
        book_id=book_id,
        agent_role="drift_audit",
        user_content=user_content,
    )

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
        evidence_refs=[],
    )

    if result:
        report.status = result.get("status", "green")
        report.metrics = result.get("metrics", {})
        report.redline_findings = result.get("redline_findings", [])
        report.yellow_findings = result.get("yellow_findings", [])
        report.affected_entities = result.get("affected_entities", [])
        report.affected_future_nodes = result.get("affected_future_nodes", [])
        report.recommended_actions = result.get("recommended_actions", [])

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
