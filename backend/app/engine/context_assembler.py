"""ContextAssembler - builds the Context Package for a scene.
Per §12 v7.3. L0 working buffer + forced dependencies + L4 + retrieval evidence.
"""
import uuid
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import (
    OutlineNode, MemoryL4StateSnapshot, MemoryL1ChapterLedger,
    MemoryL2StageSummary, MemoryL3VolumeSummary,
    StyleVoiceCard, StyleToneAnchor, WorldRule, PlotThread
)

logger = logging.getLogger("novelforge.context")


async def assemble_context(
    db: AsyncSession,
    book_id: uuid.UUID,
    outline_node: OutlineNode,
    scene_plan: dict,
    forced_dependencies: list[dict],
    retrieved_evidence: list[dict],
    previous_scene_tail: str = "",
    current_chapter: int = 1,
) -> dict:
    """Build the full Context Package per §12.
    
    Priority:
    P0: human locked, L4, required deps, world rules (never trimmed)
    P1: Scene Plan, previous scene tail, Voice Cards
    P2: story_events, open plot threads, L1
    P3: L2/L3, retrieved evidence
    P4: technique cards
    """
    # P0: L4 states for involved characters
    l4_states = {}
    for char_id in outline_node.involved_character_ids:
        cid = uuid.UUID(char_id) if isinstance(char_id, str) else char_id
        snap = await db.execute(
            select(MemoryL4StateSnapshot).where(
                MemoryL4StateSnapshot.book_id == book_id,
                MemoryL4StateSnapshot.entity_id == cid,
                MemoryL4StateSnapshot.as_of_chapter <= current_chapter - 1,
            ).order_by(MemoryL4StateSnapshot.as_of_chapter.desc(),
                       MemoryL4StateSnapshot.version.desc()).limit(1)
        )
        s = snap.scalar_one_or_none()
        if s:
            l4_states[str(char_id)] = {"state": s.state, "locked": s.is_locked}

    # P0: World rules for this location
    rules = await db.execute(select(WorldRule).where(WorldRule.book_id == book_id))
    world_rules = [{"rule_key": r.rule_key, "description": r.description} for r in rules.scalars().all()]

    # P0: Open plot threads
    threads = await db.execute(
        select(PlotThread).where(PlotThread.book_id == book_id, PlotThread.status == "open")
    )
    open_threads = [{"id": str(t.id), "name": t.name, "planted_chapter": t.planted_chapter} for t in threads.scalars().all()]

    # P1: Voice cards
    vc = await db.execute(select(StyleVoiceCard).where(StyleVoiceCard.book_id == book_id))
    voice_cards = [{"character_id": str(v.character_id), "register": v.register,
                     "emotion_expression": v.emotion_expression,
                     "sentence_patterns": v.sentence_patterns} for v in vc.scalars().all()]

    # P1: Tone anchor
    ta = await db.execute(
        select(StyleToneAnchor).where(StyleToneAnchor.book_id == book_id)
        .order_by(StyleToneAnchor.version.desc()).limit(1)
    )
    tone = ta.scalar_one_or_none()
    tone_anchor = {
        "narrative_pov": tone.narrative_pov,
        "emotional_temperature": tone.emotional_temperature,
        "pacing": tone.pacing,
        "dialogue_narration_ratio": tone.dialogue_narration_ratio,
    } if tone else {}

    # P2: L1 recent ledgers (last 3 chapters)
    l1s = await db.execute(
        select(MemoryL1ChapterLedger).where(MemoryL1ChapterLedger.book_id == book_id)
        .order_by(MemoryL1ChapterLedger.created_at.desc()).limit(3)
    )
    l1_ledgers = [l.ledger_json for l in l1s.scalars().all()]

    # P3: L2/L3 summaries
    l2 = await db.execute(
        select(MemoryL2StageSummary).where(MemoryL2StageSummary.book_id == book_id)
        .order_by(MemoryL2StageSummary.chapter_range_end.desc()).limit(1)
    )
    l2_summary = l2.scalar_one_or_none()

    l3 = await db.execute(
        select(MemoryL3VolumeSummary).where(MemoryL3VolumeSummary.book_id == book_id)
        .order_by(MemoryL3VolumeSummary.volume_no.desc()).limit(1)
    )
    l3_summary = l3.scalar_one_or_none()

    context = {
        "book_id": str(book_id),
        "outline": {
            "chapter_no": outline_node.chapter_no,
            "goal": outline_node.goal,
            "required_beats": outline_node.required_beats,
            "forbidden_outcomes": outline_node.forbidden_outcomes,
        },
        "forced_dependencies": forced_dependencies,
        "l4_state": l4_states,
        "world_rules": world_rules,
        "open_plot_threads": open_threads,
        "previous_scene_tail": previous_scene_tail,
        "l1_recent_ledgers": l1_ledgers,
        "l2_stage_summary": l2_summary.summary_json if l2_summary else {},
        "l3_volume_summary": l3_summary.summary_json if l3_summary else {},
        "voice_cards": voice_cards,
        "tone_anchor": tone_anchor,
        "retrieved_evidence": retrieved_evidence,
        "exclusions": [],
        "retrieval_meta": {
            "degraded": False,
            "sql_candidate_count": 0,
            "selected_count": len(retrieved_evidence),
        },
    }

    return context
