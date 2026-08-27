"""Idempotently materialize an approved production pack into NovelForge."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Book,
    BookProfile,
    BookSetting,
    CharacterCard,
    CharacterCoreAnchor,
    CharacterRelationship,
    LocationCard,
    MemoryL4StateSnapshot,
    OutlineDependency,
    OutlineNode,
    OutlineVersion,
    OutlineVolume,
    PlotThread,
    StyleProfile,
    StyleToneAnchor,
    StyleVoiceCard,
    WorldRule,
    WritingConstraint,
)
from app.production_pack.contracts import ProductionPack, ProductionPackValidationError, validate_pack
from app.style.metrics import compute_fingerprint


_NAMESPACE = uuid.UUID("c81806ea-9ef7-4fba-8de4-dcd37e43f429")


class ProductionPackConflictError(RuntimeError):
    """The deterministic book ID already belongs to another pack revision."""


def stable_id(pack_id: str, entity_type: str, key: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{pack_id}:{entity_type}:{key}")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _source_ref(pack: ProductionPack, kind: str, key: str) -> list[dict]:
    return [
        {
            "production_pack_id": pack.pack_id,
            "production_pack_revision": pack.revision,
            "kind": kind,
            "key": key,
            "rights": "original_blueprint",
        }
    ]


async def install_production_pack(db: AsyncSession, pack: ProductionPack) -> dict:
    """Install a validated pack in one caller-owned transaction.

    The function never deletes or rewrites an existing book.  Re-running the
    exact same pack is a zero-write success; a changed revision requires an
    explicit migration rather than an implicit destructive replacement.
    """
    report = validate_pack(pack)
    if not report.passed:
        raise ProductionPackValidationError(report)

    book_id = stable_id(pack.pack_id, "book", "root")
    existing = (
        await db.execute(select(Book).where(Book.id == book_id))
    ).scalar_one_or_none()
    if existing is not None:
        settings = (
            await db.execute(
                select(BookSetting).where(
                    BookSetting.book_id == book_id,
                    BookSetting.key.in_(["production_pack_id", "production_pack_sha256"]),
                )
            )
        ).scalars().all()
        current = {row.key: row.value for row in settings}
        if (
            current.get("production_pack_id") == pack.pack_id
            and current.get("production_pack_sha256") == report.pack_sha256
        ):
            return {
                "status": "reused",
                "book_id": str(book_id),
                "pack_id": pack.pack_id,
                "revision": pack.revision,
                "pack_sha256": report.pack_sha256,
                "counts": report.counts,
            }
        raise ProductionPackConflictError(
            f"book {book_id} already exists with a different production pack"
        )

    now = datetime.now(timezone.utc)
    book = Book(
        id=book_id,
        title=pack.book.title,
        subtitle=pack.book.subtitle,
        description=pack.book.synopsis,
        logline=pack.book.logline,
        synopsis=pack.book.synopsis,
        genre=pack.book.genre,
        tags=pack.book.tags,
        tone_summary=pack.book.tone,
        target_chapters=pack.book.target_chapters,
        target_words=pack.book.target_chars,
        planned_chapters=pack.book.target_chapters,
        status="active",
        lifecycle_status="planned",
        last_activity_at=now,
    )
    db.add(book)
    db.add(
        BookProfile(
            id=stable_id(pack.pack_id, "book_profile", "v1"),
            book_id=book_id,
            logline=pack.book.logline,
            synopsis=pack.book.synopsis,
            genre=pack.book.genre,
            themes=[pack.book.theme_question],
            tone=pack.book.tone,
            audience=pack.book.audience,
            content_boundaries=pack.reader_contract.content_boundaries,
            core_loop=(
                "新世界坠入危机 → 多方协商界契 → 以选择和代价稳定世界 → "
                "关系与权力重新分配 → 新债务推动下一世界"
            ),
            extra={
                "production_pack_id": pack.pack_id,
                "production_pack_revision": pack.revision,
                "reader_contract": pack.reader_contract.model_dump(mode="json"),
            },
        )
    )

    settings = {
        "production_pack_id": pack.pack_id,
        "production_pack_revision": str(pack.revision),
        "production_pack_sha256": report.pack_sha256,
        "chapter_target_chars": _json(pack.book.chapter_target_chars),
        "reader_contract": _json(pack.reader_contract.model_dump(mode="json")),
        "candidate_decision": _json([item.model_dump(mode="json") for item in pack.candidates]),
        "source_policy": _json([item.model_dump(mode="json") for item in pack.sources]),
        "narrative_mechanisms": _json([item.model_dump(mode="json") for item in pack.mechanisms]),
        "prospective_event_graph": _json(pack.event_graph.model_dump(mode="json")),
        "initial_relationship_ledger": _json(
            [item.model_dump(mode="json") for item in pack.relationships]
        ),
        "reference_residue_denylist": _json(pack.reference_residue_denylist),
    }
    for key, value in settings.items():
        db.add(
            BookSetting(
                id=stable_id(pack.pack_id, "book_setting", key),
                book_id=book_id,
                key=key,
                value=value,
            )
        )

    character_ids: dict[str, uuid.UUID] = {}
    for item in pack.characters:
        character_id = stable_id(pack.pack_id, "character", item.character_id)
        character_ids[item.character_id] = character_id
        core = item.model_dump(mode="json", exclude={"voice", "anchors"})
        db.add(
            CharacterCard(
                id=character_id,
                book_id=book_id,
                name=item.name,
                role=item.role,
                description=(
                    f"外在目标：{item.external_goal}\n内在需要：{item.internal_need}\n"
                    f"矛盾：{item.contradiction}\n人物弧问题：{item.arc_question}"
                ),
                card_json={
                    **core,
                    "voice": item.voice,
                    "source": "production_pack",
                },
                source_refs=_source_ref(pack, "character", item.character_id),
                version=pack.revision,
            )
        )
        for anchor in item.anchors:
            db.add(
                CharacterCoreAnchor(
                    id=stable_id(
                        pack.pack_id,
                        "character_anchor",
                        f"{item.character_id}:{anchor.code}",
                    ),
                    book_id=book_id,
                    character_id=character_id,
                    anchor_code=anchor.code,
                    anchor_type=anchor.anchor_type,
                    statement=anchor.statement,
                    priority=anchor.priority,
                    rigidity=anchor.rigidity,
                    source_kind="production_pack",
                    source_ref=_source_ref(pack, "character_anchor", anchor.code)[0],
                    status="active",
                    is_locked=True,
                )
            )
        voice = item.voice
        db.add(
            StyleVoiceCard(
                id=stable_id(pack.pack_id, "voice_card", item.character_id),
                book_id=book_id,
                character_id=character_id,
                version=pack.revision,
                register=voice.get("register"),
                sentence_patterns=voice.get("sentence_patterns") or [],
                vocabulary_preferences=voice.get("vocabulary_preferences") or [],
                addressing_rules=voice.get("addressing_rules") or {},
                emotion_expression=voice.get("emotion_expression"),
                taboo_phrases=voice.get("taboo_phrases") or [],
                # Never put reference prose into a drafting context field.
                approved_examples=[],
            )
        )

    initial_state_source_id = stable_id(pack.pack_id, "state_source_run", "chapter-0")
    relationships_by_character: dict[str, dict[str, dict]] = {
        key: {} for key in character_ids
    }
    for rel in pack.relationships:
        from_id = str(character_ids[rel.from_character_id])
        to_id = str(character_ids[rel.to_character_id])
        shared = {
            "relation_type": rel.relation_type,
            "trust": rel.trust,
            "dependence": rel.dependence,
            "fear": rel.fear,
            "desire": rel.desire,
            "mutual_misunderstanding": rel.mutual_misunderstanding,
            "as_of_chapter": 0,
        }
        relationships_by_character[rel.from_character_id][to_id] = {
            **shared,
            "wants": rel.from_wants,
        }
        relationships_by_character[rel.to_character_id][from_id] = {
            **shared,
            "wants": rel.to_wants,
        }
    for item in pack.characters:
        character_id = character_ids[item.character_id]
        initial_state = {
            "physical": {"status": "stable", "injuries": [], "location": None},
            "knowledge": {"facts": {}, "sources": {}},
            "beliefs": {
                "initial_false_belief": {
                    "statement": item.false_belief,
                    "polarity": 1,
                    "confidence": 0.75,
                    "source_event_ids": [],
                    "last_updated_chapter": 0,
                }
            },
            "goals": {
                "external_goal": {
                    "description": item.external_goal,
                    "status": "active",
                    "priority": 0.9,
                    "caused_by_event_ids": [],
                    "support_anchor_ids": [],
                },
                "internal_need": {
                    "description": item.internal_need,
                    "status": "active",
                    "priority": 0.6,
                    "caused_by_event_ids": [],
                    "support_anchor_ids": [],
                },
            },
            "relationships": relationships_by_character[item.character_id],
            "affect": {
                "vad": {"valence": 0.0, "arousal": 0.25, "dominance": 0.0},
                "label": "baseline",
            },
            "commitments": {},
            "inventory": {},
            "abilities": {"competence": item.competence, "blind_spot": item.blind_spot},
            "misunderstandings": {"self_story": item.self_story},
            "open_questions": {"arc_question": item.arc_question},
        }
        db.add(
            MemoryL4StateSnapshot(
                id=stable_id(pack.pack_id, "initial_l4_state", item.character_id),
                book_id=book_id,
                entity_type="character",
                entity_id=character_id,
                as_of_chapter=0,
                state=initial_state,
                version=pack.revision,
                status="verified",
                source_run_id=initial_state_source_id,
                is_locked=True,
            )
        )

    for rule in pack.world.rules:
        db.add(
            WorldRule(
                id=stable_id(pack.pack_id, "world_rule", rule.rule_id),
                book_id=book_id,
                rule_key=rule.rule_id,
                description=rule.statement,
                rule_json=rule.model_dump(mode="json"),
                source_refs=_source_ref(pack, "world_rule", rule.rule_id),
                version=pack.revision,
            )
        )
    for location in pack.world.locations:
        db.add(
            LocationCard(
                id=stable_id(pack.pack_id, "location", location.location_id),
                book_id=book_id,
                name=location.name,
                description=location.description,
                environment=location.environment,
                resources=location.resources,
                rules=location.rules,
                source_refs=_source_ref(pack, "location", location.location_id),
                status="active",
            )
        )

    for rel in pack.relationships:
        db.add(
            CharacterRelationship(
                id=stable_id(pack.pack_id, "relationship", rel.relationship_id),
                book_id=book_id,
                from_character_id=character_ids[rel.from_character_id],
                to_character_id=character_ids[rel.to_character_id],
                relation_type=rel.relation_type,
                stage="initial",
                start_chapter_no=rel.start_chapter_no,
                description=_json(rel.model_dump(mode="json")),
                source_refs=_source_ref(pack, "relationship", rel.relationship_id),
                status="active",
            )
        )

    plot_thread_ids: dict[str, uuid.UUID] = {}
    for thread in pack.plot_threads:
        thread_id = stable_id(pack.pack_id, "plot_thread", thread.thread_id)
        plot_thread_ids[thread.thread_id] = thread_id
        db.add(
            PlotThread(
                id=thread_id,
                book_id=book_id,
                name=thread.name,
                description=(
                    f"{thread.description}\n计划回收章：{thread.planned_payoff_chapter}"
                ),
                status="open",
                planted_chapter=thread.plant_chapter,
                source_refs=_source_ref(pack, "plot_thread", thread.thread_id),
                version=pack.revision,
            )
        )

    outline_id = stable_id(pack.pack_id, "outline", f"v{pack.revision}")
    db.add(
        OutlineVersion(
            id=outline_id,
            book_id=book_id,
            version=pack.revision,
            status="approved",
            source="production_pack",
            raw_outline=None,
            parsed_json={
                "pack_id": pack.pack_id,
                "pack_sha256": report.pack_sha256,
                "event_graph": pack.event_graph.model_dump(mode="json"),
            },
        )
    )
    for volume in pack.volumes:
        db.add(
            OutlineVolume(
                id=stable_id(pack.pack_id, "volume", str(volume.volume_no)),
                book_id=book_id,
                outline_version_id=outline_id,
                volume_no=volume.volume_no,
                title=volume.title,
                chapter_from=volume.chapter_from,
                chapter_to=volume.chapter_to,
                goal=volume.question,
                themes=volume.themes,
                required_outcomes=[volume.irreversible_end_state, volume.next_problem],
                forbidden_outcomes=["用临时设定取消本卷已支付的代价"],
                involved_character_ids=[
                    str(character_ids[key]) for key in volume.involved_character_ids
                ],
                source_refs=_source_ref(pack, "volume", str(volume.volume_no)),
            )
        )

    node_ids = {
        chapter.chapter_no: stable_id(pack.pack_id, "outline_node", str(chapter.chapter_no))
        for chapter in pack.chapters
    }
    for chapter in pack.chapters:
        required_beats = [
            f"主动者 {chapter.active_driver} 为实现即时目标采取行动：{chapter.goal}",
            f"主动阻力：{chapter.opposition}",
            f"场景或章节转折：{chapter.turn}",
            f"不可逆代价：{chapter.cost}",
            f"兑现旧承诺：{chapter.payoff}",
            f"新增叙事义务：{chapter.new_question}",
        ]
        db.add(
            OutlineNode(
                id=node_ids[chapter.chapter_no],
                book_id=book_id,
                outline_version_id=outline_id,
                node_type="chapter",
                volume_no=chapter.volume_no,
                chapter_no=chapter.chapter_no,
                title=chapter.title,
                goal=chapter.goal,
                required_beats=required_beats,
                forbidden_outcomes=chapter.forbidden_outcomes,
                involved_character_ids=[
                    str(character_ids[key]) for key in chapter.involved_character_ids
                ],
                plot_thread_ids=[
                    str(plot_thread_ids[key]) for key in chapter.plot_thread_ids
                ],
                depends_on=[str(node_ids[number]) for number in chapter.depends_on_chapters],
                expected_state_changes=[
                    {"kind": "turn", "description": chapter.turn},
                    {"kind": "cost", "description": chapter.cost},
                    {"kind": "obligation", "description": chapter.new_question},
                ],
                source_refs=_source_ref(pack, "chapter_contract", str(chapter.chapter_no)),
            )
        )
        for dependency_no in chapter.depends_on_chapters:
            db.add(
                OutlineDependency(
                    id=stable_id(
                        pack.pack_id,
                        "outline_dependency",
                        f"{dependency_no}->{chapter.chapter_no}",
                    ),
                    book_id=book_id,
                    outline_version_id=outline_id,
                    source_node_id=node_ids[dependency_no],
                    target_node_id=node_ids[chapter.chapter_no],
                    dependency_type="requires",
                    required=True,
                    required_state="predecessor_finalized",
                )
            )

    for constraint in pack.writing_constraints:
        db.add(
            WritingConstraint(
                id=stable_id(pack.pack_id, "writing_constraint", constraint.constraint_id),
                book_id=book_id,
                scope_type="book",
                constraint_type=constraint.constraint_type,
                title=constraint.title,
                body=constraint.body,
                priority=constraint.priority,
                is_hard=constraint.is_hard,
                status="active",
                source_refs=_source_ref(pack, "writing_constraint", constraint.constraint_id),
                version=pack.revision,
            )
        )

    tone = pack.style.tone_anchor
    db.add(
        StyleToneAnchor(
            id=stable_id(pack.pack_id, "tone_anchor", f"v{pack.revision}"),
            book_id=book_id,
            version=pack.revision,
            narrative_pov=tone.get("narrative_pov"),
            narrative_distance=tone.get("narrative_distance"),
            emotional_temperature=tone.get("emotional_temperature"),
            imagery_density=tone.get("imagery_density"),
            description_density=tone.get("description_density"),
            pacing=tone.get("pacing"),
            humor_level=tone.get("humor_level"),
            psychology_ratio=tone.get("psychology_ratio"),
            dialogue_narration_ratio=tone.get("dialogue_narration_ratio"),
            adult_violence_expression=tone.get("adult_violence_expression"),
            forbidden_modern_expressions=tone.get("forbidden_modern_expressions") or [],
            approved_samples=[],
        )
    )
    db.add(
        StyleProfile(
            id=stable_id(pack.pack_id, "style_profile", f"v{pack.revision}"),
            book_id=book_id,
            version=pack.revision,
            status="approved",
            metric_vector=pack.style.metric_vector,
            metric_ranges=pack.style.metric_ranges,
            fingerprint=compute_fingerprint(pack.style.metric_vector),
            narrative_profile=pack.style.narrative,
            dialogue_profile=pack.style.dialogue,
            rhythm_profile=pack.style.rhythm,
            emotion_expression_profile=pack.style.emotion_expression,
            technique_profile={"techniques": pack.style.techniques},
            scene_mode_profiles=pack.style.scene_modes,
            confidence_by_dimension={
                "overall": pack.style.confidence,
                "source_count": len(pack.style.source_ids),
                "note": "single-source traits remain project-specific, not universal",
            },
            analyzer_version="production_pack_v1",
            metric_engine_version="1.0",
            approved_by="production_pack",
            approved_at=now,
        )
    )

    await db.flush()
    return {
        "status": "installed",
        "book_id": str(book_id),
        "outline_version": pack.revision,
        "pack_id": pack.pack_id,
        "revision": pack.revision,
        "pack_sha256": report.pack_sha256,
        "counts": report.counts,
    }
