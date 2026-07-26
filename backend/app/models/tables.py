"""All ORM models for NovelForge - 40+ tables per v7.3 spec."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Integer, String, Text, Boolean, DateTime, Float, ForeignKey, Index, UniqueConstraint, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY, TSVECTOR
from app.models.base import Base, utcnow, TimestampMixin, BookMixin, VersionMixin


def gen_uuid():
    return uuid.uuid4()


# ---- Core book tables ----
class Book(Base, TimestampMixin):
    __tablename__ = "books"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="created", nullable=False)
    target_chapters: Mapped[int] = mapped_column(Integer, default=500)
    target_words: Mapped[int] = mapped_column(Integer, default=5000000)
    finalized_chapters: Mapped[int] = mapped_column(Integer, default=0)
    finalized_words: Mapped[int] = mapped_column(Integer, default=0)


class BookSetting(Base, TimestampMixin):
    __tablename__ = "book_settings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (UniqueConstraint("book_id", "key"),)


# ---- Outline tables ----
class OutlineVersion(Base, TimestampMixin):
    __tablename__ = "outline_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="upload", nullable=False)
    raw_outline: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    __table_args__ = (UniqueConstraint("book_id", "version"),)


class OutlineNode(Base, TimestampMixin):
    __tablename__ = "outline_nodes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    outline_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outline_versions.id"), nullable=False, index=True)
    node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    volume_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    required_beats: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    forbidden_outcomes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    involved_character_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    plot_thread_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    depends_on: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    expected_state_changes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    __table_args__ = (UniqueConstraint("book_id", "outline_version_id", "chapter_no"), Index("idx_outline_nodes_book_chapter", "book_id", "chapter_no"))


class OutlineDependency(Base, TimestampMixin):
    __tablename__ = "outline_dependencies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    outline_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outline_versions.id"), nullable=False, index=True)
    source_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    dependency_type: Mapped[str] = mapped_column(String(100), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    required_state: Mapped[str | None] = mapped_column(String(200), nullable=True)


# ---- Chapter tables ----
class ChapterTask(Base, TimestampMixin):
    __tablename__ = "chapter_tasks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), default="queued", nullable=False, index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # P0-04 lease / recovery fields
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class Chapter(Base, TimestampMixin):
    __tablename__ = "chapters"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False)
    outline_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="queued", nullable=False, index=True)
    finalized_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    __table_args__ = (UniqueConstraint("book_id", "chapter_no"),)


class ChapterVersion(Base, TimestampMixin):
    __tablename__ = "chapter_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    source_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    __table_args__ = (UniqueConstraint("chapter_id", "version"),)


# ---- Scene & paragraph tables ----
class Scene(Base, TimestampMixin):
    __tablename__ = "scenes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=False, index=True)
    scene_no: Mapped[int] = mapped_column(Integer, nullable=False)
    pov_character_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    outline_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    canon_status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __table_args__ = (UniqueConstraint("chapter_id", "scene_no", "version"),)


class Paragraph(Base, TimestampMixin):
    __tablename__ = "paragraphs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=False, index=True)
    scene_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("scenes.id"), nullable=False, index=True)
    paragraph_key: Mapped[str] = mapped_column(String(50), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __table_args__ = (UniqueConstraint("scene_id", "paragraph_key", "version"),)


# ---- Character tables ----
class CharacterCard(Base, TimestampMixin):
    __tablename__ = "character_cards"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    card_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CharacterStateEvent(Base, TimestampMixin):
    __tablename__ = "character_state_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    character_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    field: Mapped[str] = mapped_column(String(200), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class CharacterStateSnapshot(Base, TimestampMixin):
    __tablename__ = "character_state_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    character_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    as_of_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (UniqueConstraint("book_id", "character_id", "as_of_chapter", "version"),)


# ---- World & plot tables ----
class WorldRule(Base, TimestampMixin):
    __tablename__ = "world_rules"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    rule_key: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    rule_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PlotThread(Base, TimestampMixin):
    __tablename__ = "plot_threads"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="open", nullable=False, index=True)
    planted_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RelationshipEvent(Base, TimestampMixin):
    __tablename__ = "relationship_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    character_a_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    character_b_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    old_state: Mapped[str | None] = mapped_column(String(200), nullable=True)
    new_state: Mapped[str] = mapped_column(String(200), nullable=False)
    evidence: Mapped[str | None] = mapped_column(Text, nullable=True)


class ItemEvent(Base, TimestampMixin):
    __tablename__ = "item_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    character_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class TimelineEvent(Base, TimestampMixin):
    __tablename__ = "timeline_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    world_time_text: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


# ---- Story events (unified ledger) ----
class StoryEvent(Base, TimestampMixin):
    __tablename__ = "story_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scene_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_entity_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    object_entity_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    plot_thread_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    before_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cause_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    world_time_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    evidence_paragraph_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evidence_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    certainty: Mapped[str] = mapped_column(String(50), default="explicit", nullable=False)
    canon_status: Mapped[str] = mapped_column(String(50), default="canon", nullable=False)
    source_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __table_args__ = (
        Index("idx_story_events_book_chapter", "book_id", "chapter_id"),
    )


class EntityAlias(Base, TimestampMixin):
    __tablename__ = "entity_aliases"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    alias: Mapped[str] = mapped_column(String(200), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)


# ---- Search documents ----
class SceneSearchDocument(Base, TimestampMixin):
    __tablename__ = "scene_search_documents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scene_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False)
    scene_no: Mapped[int] = mapped_column(Integer, nullable=False)
    outline_node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    pov_character_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    character_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    location_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    item_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    plot_thread_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    event_types: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    scene_summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    search_tsv: Mapped[str] = mapped_column(Text, nullable=False, default="")
    canon_status: Mapped[str] = mapped_column(String(50), default="canon", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __table_args__ = (
        Index("idx_scene_search_book", "book_id", "canon_status", "chapter_no"),
        UniqueConstraint("scene_id", "content_hash", "version"),
    )


# ---- Memory tables L1-L4 ----
class MemoryL1ChapterLedger(Base, TimestampMixin):
    __tablename__ = "memory_l1_chapter_ledgers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    finalized_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="generated", nullable=False)
    ledger_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    __table_args__ = (UniqueConstraint("book_id", "chapter_id", "finalized_version"),)


class MemoryL2StageSummary(Base, TimestampMixin):
    __tablename__ = "memory_l2_stage_summaries"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_range_start: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_range_end: Mapped[int] = mapped_column(Integer, nullable=False)
    outline_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="generated", nullable=False)
    summary_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    __table_args__ = (UniqueConstraint("book_id", "chapter_range_start", "chapter_range_end", "outline_version"),)


class MemoryL3VolumeSummary(Base, TimestampMixin):
    __tablename__ = "memory_l3_volume_summaries"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    volume_no: Mapped[int] = mapped_column(Integer, nullable=False)
    outline_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="generated", nullable=False)
    summary_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    __table_args__ = (UniqueConstraint("book_id", "volume_no", "outline_version"),)


class MemoryL4StateSnapshot(Base, TimestampMixin):
    __tablename__ = "memory_l4_state_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    as_of_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(50), default="verified", nullable=False)
    source_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (UniqueConstraint("book_id", "entity_type", "entity_id", "as_of_chapter", "version"),)


# ---- Style tables ----
class StyleVoiceCard(Base, TimestampMixin):
    __tablename__ = "style_voice_cards"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    character_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    register: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentence_patterns: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    vocabulary_preferences: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    addressing_rules: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    emotion_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    taboo_phrases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    approved_examples: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class StyleToneAnchor(Base, TimestampMixin):
    __tablename__ = "style_tone_anchors"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    narrative_pov: Mapped[str | None] = mapped_column(String(200), nullable=True)
    narrative_distance: Mapped[str | None] = mapped_column(String(200), nullable=True)
    emotional_temperature: Mapped[str | None] = mapped_column(String(200), nullable=True)
    imagery_density: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description_density: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pacing: Mapped[str | None] = mapped_column(String(200), nullable=True)
    humor_level: Mapped[str | None] = mapped_column(String(200), nullable=True)
    psychology_ratio: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dialogue_narration_ratio: Mapped[str | None] = mapped_column(String(200), nullable=True)
    adult_violence_expression: Mapped[str | None] = mapped_column(Text, nullable=True)
    forbidden_modern_expressions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    approved_samples: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    anchor_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


# ---- Query & retrieval tables ----
class QueryPlan(Base, TimestampMixin):
    __tablename__ = "query_plans"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scene_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    plan_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)


class RetrievalRun(Base, TimestampMixin):
    __tablename__ = "retrieval_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    query_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("query_plans.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="running", nullable=False)
    degraded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    selected_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RetrievalCandidate(Base, TimestampMixin):
    __tablename__ = "retrieval_candidates"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    retrieval_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("retrieval_runs.id"), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_scene: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rule_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    full_text_rank: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    forced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    candidate_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class RetrievalJudgement(Base, TimestampMixin):
    __tablename__ = "retrieval_judgements"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    retrieval_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("retrieval_runs.id"), nullable=False, index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    relevance: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_mode: Mapped[str] = mapped_column(String(100), nullable=False)


# ---- Review & patch tables ----
class ReviewIssue(Base, TimestampMixin):
    __tablename__ = "review_issues"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scene_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issue_cluster_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    paragraph_id: Mapped[str] = mapped_column(String(50), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(50), default="minor", nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    repair_instruction: Mapped[str | None] = mapped_column(Text, nullable=True)
    protected_facts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class RewritePatch(Base, TimestampMixin):
    __tablename__ = "rewrite_patches"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scene_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    paragraph_id: Mapped[str] = mapped_column(String(50), nullable=False)
    chapter_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    replacement_text: Mapped[str] = mapped_column(Text, nullable=False)
    preserved_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    preserved_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_issue_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    retry_round: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


# ---- DriftAudit ----
class DriftAuditReport(Base):
    __tablename__ = "drift_audit_reports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_range_start: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_range_end: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    redline_findings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    yellow_findings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    affected_entities: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    affected_future_nodes: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    recommended_actions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    evidence_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# ---- Agent run tables ----
class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scene_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_role: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), default="running", nullable=False, index=True)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    __table_args__ = (Index("idx_agent_runs_idempotency", "idempotency_key"),)


class AgentRunOutput(Base, TimestampMixin):
    __tablename__ = "agent_run_outputs"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"), primary_key=True)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    agent_role: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(200), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    raw_provider_response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reasoning_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    publishable_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    inline_leak_detected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    leak_status: Mapped[str] = mapped_column(String(50), default="unchecked", nullable=False)
    output_integrity: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    parser_version: Mapped[str] = mapped_column(String(50), default="v1", nullable=False)
    guard_version: Mapped[str] = mapped_column(String(50), default="v1", nullable=False)


class LlmUsageEvent(Base, TimestampMixin):
    __tablename__ = "llm_usage_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(200), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)


# ---- Human intervention ----
class HumanIntervention(Base, TimestampMixin):
    __tablename__ = "human_interventions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    intervention_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    old_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# ---- Prompt templates ----
class PromptTemplate(Base, TimestampMixin):
    __tablename__ = "prompt_templates"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    agent_role: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    input_variables: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    output_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("agent_role", "version"),)


# ---- Technique cards ----
class TechniqueCard(Base, TimestampMixin):
    __tablename__ = "technique_cards"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    genre_tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    applicable_conditions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    contraindications: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    approved_by_human: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# ═══════════════════════════════════════════════════════════════════════════════
# v7.4 Tables: Model Bindings + Change Log + Route Events + Context Packages
# ═══════════════════════════════════════════════════════════════════════════════

class AgentModelBinding(Base):
    __tablename__ = "agent_model_bindings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'global' or 'book'
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_role: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    primary_model: Mapped[str] = mapped_column(String(200), nullable=False)
    fallback_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reasoning_mode: Mapped[str] = mapped_column(String(30), nullable=False, default="auto")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ModelChangeLog(Base):
    __tablename__ = "model_change_log"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    binding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_model_bindings.id"), nullable=False)
    agent_role: Mapped[str] = mapped_column(String(100), nullable=False)
    old_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    old_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    new_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    new_model: Mapped[str] = mapped_column(String(200), nullable=False)
    old_reasoning_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    new_reasoning_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[str] = mapped_column(String(200), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ModelRouteEvent(Base):
    __tablename__ = "model_route_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_role: Mapped[str] = mapped_column(String(100), nullable=False)
    configured_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    configured_model: Mapped[str] = mapped_column(String(200), nullable=False)
    actual_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    actual_model: Mapped[str] = mapped_column(String(200), nullable=False)
    route_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 'primary', 'retry', 'fallback'
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentContextPackage(Base):
    __tablename__ = "agent_context_packages"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=True)
    scene_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("scenes.id"), nullable=True)
    agent_role: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_template_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    context_schema_version: Mapped[str] = mapped_column(String(50), nullable=False)
    assembler_version: Mapped[str] = mapped_column(String(50), nullable=False)
    request_parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    assembly_manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    l4_entity_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    l1_ledger_refs: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}")
    l2_summary_refs: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}")
    l3_summary_refs: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}")
    genre_profile_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("genre_profiles.id"), nullable=True)
    story_evidence_refs: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}")
    external_evidence_refs: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}")
    assembled_token_estimate: Mapped[int] = mapped_column(Integer, nullable=False)
    rendered_prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    publish_state: Mapped[str] = mapped_column(String(50), nullable=False)
    block_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    assembled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReferenceSample(Base):
    __tablename__ = "reference_samples"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    original_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    compressed_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    genre_hint: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class GenreProfile(Base):
    __tablename__ = "genre_profiles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    narrative_person: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pacing_profile: Mapped[dict] = mapped_column(JSONB, nullable=False)
    technique_tags: Mapped[list] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    lexical_tendency: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_intensity_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_injection_snippet: Mapped[str] = mapped_column(Text, nullable=False)
    analyzer_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
    sanitizer_report: Mapped[dict] = mapped_column(JSONB, nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchSession(Base):
    __tablename__ = "research_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=True)
    outline_node_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("outline_nodes.id"), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    requested_topic: Mapped[str] = mapped_column(Text, nullable=False)
    plan_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=True)
    synthesis_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExternalResearchEvidence(Base):
    __tablename__ = "external_research_evidence"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    research_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("research_sessions.id", ondelete="CASCADE"), nullable=False)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    source_domain: Mapped[str] = mapped_column(String(500), nullable=False)
    source_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trust_tier: Mapped[str] = mapped_column(String(50), nullable=False)
    relevance: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    conflicts_or_uncertainty: Mapped[list | None] = mapped_column(ARRAY(String), nullable=True)
    evidence_source: Mapped[str] = mapped_column(String(50), nullable=False, server_default="external_research")
    source_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=False)
