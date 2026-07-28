"""ContextAssembler with itemized Manifest (AI__.md v3.0 §10 / PR-06).

User directive (2026-07-27): budget is **record-only** — never hard-limit,
never drop required/non-required items for overflow, never block the pipeline
on context_overflow. Manifest still records estimated tokens and advisory
input_budget for observability / rebuild.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    OutlineNode,
    MemoryL4StateSnapshot,
    MemoryL1ChapterLedger,
    MemoryL2StageSummary,
    MemoryL3VolumeSummary,
    StyleVoiceCard,
    StyleToneAnchor,
    WorldRule,
    PlotThread,
    BookProfile,
    WritingConstraint,
    CharacterCard,
    CharacterRelationship,
    LocationCard,
    OutlineVolume,
    GenreProfile,
    ExternalResearchEvidence,
)
from app.token_estimate import safe_token_estimate

logger = logging.getLogger("novelforge.context")

ASSEMBLER_VERSION = "3.0-v8-wired"


def _sha(obj: Any) -> str:
    raw = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _item(
    *,
    kind: str,
    content: Any,
    priority: int,
    required: bool,
    reason: str,
    source_id: str | None = None,
    source_version: int | str | None = None,
    canon_level: str = "working",
    agent_role: str = "draft_writer",
) -> dict:
    if isinstance(content, str):
        text = content
        snapshot = content
    else:
        text = json.dumps(content, ensure_ascii=False, default=str)
        snapshot = content
    est = safe_token_estimate(text, agent_role=agent_role)
    return {
        "kind": kind,
        "source_id": source_id,
        "source_version": source_version,
        "canon_level": canon_level,
        "priority": priority,
        "content_hash": _sha(text),
        "estimated_tokens": est,
        "required": required,
        "reason": reason,
        "content_snapshot": snapshot,
        "excluded": False,
        "exclude_reason": None,
    }


def advisory_input_budget(
    context_window: int = 128000,
    max_output_tokens: int = 8192,
) -> int:
    """Advisory only — never used to block or trim."""
    reserve = max(1024, int(context_window * 0.05))
    return max(1024, int(context_window) - int(max_output_tokens) - reserve)


def build_manifest(
    items: list[dict],
    *,
    input_budget: int | None = None,
    agent_role: str = "draft_writer",
) -> dict:
    used = sum(int(i.get("estimated_tokens") or 0) for i in items if not i.get("excluded"))
    budget = int(input_budget if input_budget is not None else advisory_input_budget())
    # Record overflow flag but DO NOT exclude or block
    overflow_advisory = used > budget
    slim_for_hash = [
        {
            "kind": i["kind"],
            "source_id": i.get("source_id"),
            "content_hash": i.get("content_hash"),
            "priority": i.get("priority"),
            "required": i.get("required"),
            "estimated_tokens": i.get("estimated_tokens"),
            "excluded": i.get("excluded", False),
        }
        for i in items
    ]
    return {
        "items": items,
        "entries": slim_for_hash,  # caller/compat
        "excluded_entries": [i for i in items if i.get("excluded")],
        "manifest_hash": _sha(slim_for_hash),
        "input_budget": budget,
        "used_tokens": used,
        "budget_mode": "record_only",
        "overflow_advisory": overflow_advisory,
        "excluded": [],  # never auto-exclude under record_only
        "assembler_version": ASSEMBLER_VERSION,
        "agent_role": agent_role,
        "budget": {
            "max_context": budget + 8192,  # rough window hint
            "input_budget": budget,
            "reserved_output": 8192,
            "used": used,
            "mode": "record_only",
            "overflow_advisory": overflow_advisory,
        },
    }


async def assemble_context(
    db: AsyncSession,
    book_id: uuid.UUID,
    outline_node: OutlineNode,
    scene_plan: dict,
    forced_dependencies: list[dict],
    retrieved_evidence: list[dict],
    previous_scene_tail: str = "",
    current_chapter: int = 1,
    *,
    agent_role: str = "draft_writer",
    context_window: int = 128000,
    max_output_tokens: int = 8192,
) -> dict:
    """Build Context Package + itemized Manifest. No hard trim / no block."""
    items: list[dict] = []

    outline_payload = {
        "chapter_no": outline_node.chapter_no,
        "goal": outline_node.goal,
        "required_beats": outline_node.required_beats,
        "forbidden_outcomes": outline_node.forbidden_outcomes,
        "depends_on": getattr(outline_node, "depends_on", None),
        "involved_character_ids": outline_node.involved_character_ids or [],
    }
    items.append(
        _item(
            kind="outline_node",
            content=outline_payload,
            priority=1000,
            required=True,
            reason="current_outline",
            source_id=str(outline_node.id),
            canon_level="approved",
            agent_role=agent_role,
        )
    )

    items.append(
        _item(
            kind="forced_dependencies",
            content=forced_dependencies or [],
            priority=990,
            required=True,
            reason="required_dependencies",
            agent_role=agent_role,
        )
    )

    if scene_plan:
        items.append(
            _item(
                kind="scene_plan",
                content=scene_plan,
                priority=980,
                required=True,
                reason="current_scene_plan",
                agent_role=agent_role,
            )
        )

    # L4
    l4_states: dict[str, Any] = {}
    for char_id in outline_node.involved_character_ids or []:
        cid = uuid.UUID(char_id) if isinstance(char_id, str) else char_id
        snap = await db.execute(
            select(MemoryL4StateSnapshot)
            .where(
                MemoryL4StateSnapshot.book_id == book_id,
                MemoryL4StateSnapshot.entity_id == cid,
                MemoryL4StateSnapshot.as_of_chapter <= current_chapter - 1,
            )
            .order_by(
                MemoryL4StateSnapshot.as_of_chapter.desc(),
                MemoryL4StateSnapshot.version.desc(),
            )
            .limit(1)
        )
        s = snap.scalar_one_or_none()
        if s:
            payload = {"state": s.state, "locked": s.is_locked}
            l4_states[str(char_id)] = payload
            items.append(
                _item(
                    kind="l4_state",
                    content=payload,
                    priority=970,
                    required=True,
                    reason="entity_l4",
                    source_id=str(s.id),
                    source_version=s.version,
                    canon_level="locked" if s.is_locked else "canon",
                    agent_role=agent_role,
                )
            )

    rules = await db.execute(select(WorldRule).where(WorldRule.book_id == book_id))
    world_rules = [
        {"rule_key": r.rule_key, "description": r.description, "id": str(r.id)}
        for r in rules.scalars().all()
    ]
    for r in world_rules:
        items.append(
            _item(
                kind="world_rule",
                content=r,
                priority=960,
                required=True,
                reason="world_rule",
                source_id=r.get("id"),
                canon_level="approved",
                agent_role=agent_role,
            )
        )

    # v8.0: book_profile / volume / character cards / relationships / locations / writing constraints
    try:
        bp = (
            await db.execute(select(BookProfile).where(BookProfile.book_id == book_id))
        ).scalar_one_or_none()
        if bp:
            profile = {
                "logline": bp.logline,
                "synopsis": bp.synopsis,
                "genre": bp.genre,
                "themes": bp.themes,
                "tone": bp.tone,
                "core_loop": bp.core_loop,
            }
            items.append(
                _item(
                    kind="book_profile",
                    content=profile,
                    priority=810,
                    required=False,
                    reason="book_tone_profile",
                    source_id=str(bp.id),
                    canon_level="approved",
                    agent_role=agent_role,
                )
            )
    except Exception as e:
        logger.debug("book_profile skip: %s", e)

    try:
        vol = (
            await db.execute(
                select(OutlineVolume)
                .where(
                    OutlineVolume.book_id == book_id,
                    OutlineVolume.chapter_from.is_not(None),
                    OutlineVolume.chapter_from <= current_chapter,
                )
                .order_by(OutlineVolume.volume_no.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if vol and (vol.chapter_to is None or vol.chapter_to >= current_chapter):
            items.append(
                _item(
                    kind="current_volume",
                    content={
                        "volume_no": vol.volume_no,
                        "title": vol.title,
                        "goal": vol.goal,
                        "chapter_from": vol.chapter_from,
                        "chapter_to": vol.chapter_to,
                        "themes": vol.themes,
                    },
                    priority=940,
                    required=True,
                    reason="current_volume_goal",
                    source_id=str(vol.id),
                    canon_level="approved",
                    agent_role=agent_role,
                )
            )
    except Exception as e:
        logger.debug("volume skip: %s", e)

    try:
        cards = (
            await db.execute(select(CharacterCard).where(CharacterCard.book_id == book_id).limit(40))
        ).scalars().all()
        if cards:
            payload = [
                {"id": str(c.id), "name": c.name, "role": c.role, "description": c.description}
                for c in cards
            ]
            items.append(
                _item(
                    kind="character_cards",
                    content=payload,
                    priority=920,
                    required=True,
                    reason="character_bible",
                    canon_level="approved",
                    agent_role=agent_role,
                )
            )
    except Exception as e:
        logger.debug("character_cards skip: %s", e)

    try:
        rels = (
            await db.execute(
                select(CharacterRelationship)
                .where(CharacterRelationship.book_id == book_id, CharacterRelationship.status == "active")
                .limit(80)
            )
        ).scalars().all()
        if rels:
            payload = [
                {
                    "from": str(r.from_character_id),
                    "to": str(r.to_character_id),
                    "type": r.relation_type,
                    "stage": r.stage,
                    "description": r.description,
                }
                for r in rels
            ]
            items.append(
                _item(
                    kind="character_relationships",
                    content=payload,
                    priority=780,
                    required=False,
                    reason="relationship_graph",
                    canon_level="working",
                    agent_role=agent_role,
                )
            )
    except Exception as e:
        logger.debug("relationships skip: %s", e)

    try:
        locs = (
            await db.execute(
                select(LocationCard).where(LocationCard.book_id == book_id, LocationCard.status == "active").limit(40)
            )
        ).scalars().all()
        if locs:
            payload = [
                {"id": str(l.id), "name": l.name, "description": l.description, "rules": l.rules}
                for l in locs
            ]
            items.append(
                _item(
                    kind="location_cards",
                    content=payload,
                    priority=900,
                    required=True,
                    reason="location_rules",
                    canon_level="approved",
                    agent_role=agent_role,
                )
            )
    except Exception as e:
        logger.debug("locations skip: %s", e)

    try:
        wcs = (
            await db.execute(
                select(WritingConstraint)
                .where(WritingConstraint.book_id == book_id, WritingConstraint.status == "active")
                .order_by(WritingConstraint.priority.desc())
                .limit(60)
            )
        ).scalars().all()
        for wc in wcs:
            items.append(
                _item(
                    kind="writing_constraints",
                    content={
                        "type": wc.constraint_type,
                        "title": wc.title,
                        "body": wc.body,
                        "is_hard": wc.is_hard,
                        "scope_type": wc.scope_type,
                    },
                    priority=890 if wc.is_hard else 800,
                    required=bool(wc.is_hard),
                    reason="writing_rule",
                    source_id=str(wc.id),
                    canon_level="approved",
                    agent_role=agent_role,
                )
            )
    except Exception as e:
        logger.debug("writing_constraints skip: %s", e)

    try:
        gp = (
            await db.execute(
                select(GenreProfile)
                .where(GenreProfile.book_id == book_id, GenreProfile.status == "approved")
                .order_by(GenreProfile.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if gp:
            items.append(
                _item(
                    kind="approved_genre_profile",
                    content={
                        "id": str(gp.id),
                        "narrative_person": gp.narrative_person,
                        "pacing_profile": gp.pacing_profile,
                        "technique_tags": list(gp.technique_tags or []),
                        "prompt_injection_snippet": gp.prompt_injection_snippet,
                    },
                    priority=815,
                    required=False,
                    reason="genre_profile",
                    source_id=str(gp.id),
                    canon_level="approved",
                    agent_role=agent_role,
                )
            )
    except Exception as e:
        logger.debug("genre_profile skip: %s", e)

    try:
        evid = (
            await db.execute(
                select(ExternalResearchEvidence)
                .where(
                    ExternalResearchEvidence.book_id == book_id,
                    ExternalResearchEvidence.status == "approved",
                )
                .limit(20)
            )
        ).scalars().all()
        if evid:
            payload = [
                {
                    "id": str(e.id),
                    "title": getattr(e, "source_title", None),
                    "snippet": (getattr(e, "summary", None) or "")[:800],
                    "source_kind": "external_research",
                    "url": getattr(e, "source_url", None),
                }
                for e in evid
            ]
            items.append(
                _item(
                    kind="approved_external_research",
                    content=payload,
                    priority=350,
                    required=False,
                    reason="external_research_approved",
                    canon_level="external",
                    agent_role=agent_role,
                )
            )
    except Exception as e:
        logger.debug("external research skip: %s", e)

    threads = await db.execute(
        select(PlotThread).where(PlotThread.book_id == book_id, PlotThread.status == "open")
    )
    open_threads = [
        {"id": str(t.id), "name": t.name, "planted_chapter": t.planted_chapter}
        for t in threads.scalars().all()
    ]
    for t in open_threads:
        items.append(
            _item(
                kind="plot_thread",
                content=t,
                priority=700,
                required=False,
                reason="open_plot_thread",
                source_id=t["id"],
                agent_role=agent_role,
            )
        )

    vc = await db.execute(select(StyleVoiceCard).where(StyleVoiceCard.book_id == book_id))
    voice_cards = [
        {
            "character_id": str(v.character_id),
            "register": v.register,
            "emotion_expression": v.emotion_expression,
            "sentence_patterns": v.sentence_patterns,
            "id": str(v.id),
        }
        for v in vc.scalars().all()
    ]
    for v in voice_cards:
        items.append(
            _item(
                kind="voice_card",
                content=v,
                priority=800,
                required=False,
                reason="voice_card",
                source_id=v.get("id"),
                agent_role=agent_role,
            )
        )

    ta = await db.execute(
        select(StyleToneAnchor)
        .where(StyleToneAnchor.book_id == book_id)
        .order_by(StyleToneAnchor.version.desc())
        .limit(1)
    )
    tone = ta.scalar_one_or_none()
    tone_anchor = (
        {
            "narrative_pov": tone.narrative_pov,
            "emotional_temperature": tone.emotional_temperature,
            "pacing": tone.pacing,
            "dialogue_narration_ratio": tone.dialogue_narration_ratio,
            "version": tone.version,
            "id": str(tone.id),
        }
        if tone
        else {}
    )
    if tone_anchor:
        items.append(
            _item(
                kind="tone_anchor",
                content=tone_anchor,
                priority=810,
                required=False,
                reason="tone_anchor",
                source_id=tone_anchor.get("id"),
                source_version=tone_anchor.get("version"),
                agent_role=agent_role,
            )
        )

    if previous_scene_tail:
        items.append(
            _item(
                kind="previous_scene_tail",
                content=previous_scene_tail,
                priority=850,
                required=True,
                reason="continuity_tail",
                agent_role=agent_role,
            )
        )

    l1s = await db.execute(
        select(MemoryL1ChapterLedger)
        .where(MemoryL1ChapterLedger.book_id == book_id)
        .order_by(MemoryL1ChapterLedger.created_at.desc())
        .limit(3)
    )
    l1_ledgers = []
    for l in l1s.scalars().all():
        l1_ledgers.append(l.ledger_json)
        items.append(
            _item(
                kind="l1_ledger",
                content=l.ledger_json,
                priority=600,
                required=False,
                reason="recent_l1",
                source_id=str(l.id),
                agent_role=agent_role,
            )
        )

    l2 = await db.execute(
        select(MemoryL2StageSummary)
        .where(MemoryL2StageSummary.book_id == book_id)
        .order_by(MemoryL2StageSummary.chapter_range_end.desc())
        .limit(1)
    )
    l2_summary = l2.scalar_one_or_none()
    l2_json = l2_summary.summary_json if l2_summary else {}
    if l2_json:
        items.append(
            _item(
                kind="l2_summary",
                content=l2_json,
                priority=500,
                required=False,
                reason="stage_summary",
                source_id=str(l2_summary.id) if l2_summary else None,
                agent_role=agent_role,
            )
        )

    l3 = await db.execute(
        select(MemoryL3VolumeSummary)
        .where(MemoryL3VolumeSummary.book_id == book_id)
        .order_by(MemoryL3VolumeSummary.volume_no.desc())
        .limit(1)
    )
    l3_summary = l3.scalar_one_or_none()
    l3_json = l3_summary.summary_json if l3_summary else {}
    if l3_json:
        items.append(
            _item(
                kind="l3_summary",
                content=l3_json,
                priority=400,
                required=False,
                reason="volume_summary",
                source_id=str(l3_summary.id) if l3_summary else None,
                agent_role=agent_role,
            )
        )

    for i, ev in enumerate(retrieved_evidence or []):
        items.append(
            _item(
                kind="retrieved_evidence",
                content=ev,
                priority=300 - min(i, 50),
                required=False,
                reason="retrieval",
                source_id=str(ev.get("id") or ev.get("scene_id") or i),
                agent_role=agent_role,
            )
        )

    budget = advisory_input_budget(context_window, max_output_tokens)
    manifest = build_manifest(items, input_budget=budget, agent_role=agent_role)

    # Legacy flat package for draft_writer prompts (unchanged shape)
    context = {
        "book_id": str(book_id),
        "outline": outline_payload,
        "forced_dependencies": forced_dependencies,
        "l4_state": l4_states,
        "world_rules": [{"rule_key": r["rule_key"], "description": r["description"]} for r in world_rules],
        "open_plot_threads": open_threads,
        "previous_scene_tail": previous_scene_tail,
        "l1_recent_ledgers": l1_ledgers,
        "l2_stage_summary": l2_json,
        "l3_volume_summary": l3_json,
        "voice_cards": [
            {
                "character_id": v["character_id"],
                "register": v["register"],
                "emotion_expression": v["emotion_expression"],
                "sentence_patterns": v.get("sentence_patterns"),
            }
            for v in voice_cards
        ],
        "tone_anchor": {
            k: tone_anchor[k]
            for k in (
                "narrative_pov",
                "emotional_temperature",
                "pacing",
                "dialogue_narration_ratio",
            )
            if k in tone_anchor
        }
        if tone_anchor
        else {},
        "retrieved_evidence": retrieved_evidence,
        "exclusions": [],  # record-only: never populated by budget
        "retrieval_meta": {
            "degraded": False,
            "sql_candidate_count": 0,
            "selected_count": len(retrieved_evidence or []),
        },
        # PR-06
        "assembly_manifest": manifest,
        "manifest_hash": manifest["manifest_hash"],
        "used_tokens": manifest["used_tokens"],
        "input_budget": manifest["input_budget"],
        "budget_mode": "record_only",
        "overflow_advisory": manifest["overflow_advisory"],
        "items": items,
    }

    if manifest["overflow_advisory"]:
        logger.info(
            "context budget advisory overflow book=%s used=%s budget=%s (record_only, not blocked)",
            book_id,
            manifest["used_tokens"],
            manifest["input_budget"],
        )

    return context
