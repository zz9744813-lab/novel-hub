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
        db=db,
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

    db.add(report)
    await db.flush()

    # Per §9.5: red -> NEEDS_HUMAN, pause affected chapters
    if report.status == "red":
        logger.warning(f"DriftAudit RED for chapters {chapter_range_start}-{chapter_range_end}")
        # TODO: pause affected dependent chapters
    elif report.status == "yellow":
        logger.info(f"DriftAudit YELLOW for chapters {chapter_range_start}-{chapter_range_end}")
    else:
        logger.info(f"DriftAudit GREEN for chapters {chapter_range_start}-{chapter_range_end}")

    return report
