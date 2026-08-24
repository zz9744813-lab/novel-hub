"""All ORM models for NovelForge - 40+ tables per v7.3 spec."""
import uuid
from datetime import datetime
from sqlalchemy import (
    Integer, String, Text, Boolean, DateTime, Float, ForeignKey, Index,
    UniqueConstraint, BigInteger, func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from app.models.base import Base, utcnow, TimestampMixin

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
    # v8.0 library fields
    subtitle: Mapped[str | None] = mapped_column(String(500), nullable=True)
    logline: Mapped[str | None] = mapped_column(Text, nullable=True)
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)
    genre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    tone_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_thumb_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    planned_chapters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_chapter_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", server_default="draft")
    source_import_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    volume_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    arc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    import_artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
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
    # v9.3: human editorial status is tracked independently of the AI pipeline
    # status (spec §4) — "AI 写完了吗" vs "人工认可了吗" are separate questions.
    editorial_status: Mapped[str] = mapped_column(
        String(30), default="pending_review", nullable=False, index=True
    )  # pending_review|in_review|accepted|accepted_with_notes|revision_requested|revising|awaiting_recheck|rejected|waived
    finalized_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # P1 CORE-001: CAS / audit fields for State Transition Service
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    state_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_transition_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    active_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    __table_args__ = (UniqueConstraint("book_id", "chapter_no"),)


class ChapterStateEvent(Base, TimestampMixin):
    """Immutable audit log for chapter status transitions (P1 CORE-002)."""
    __tablename__ = "chapter_state_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    chapter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=False, index=True
    )
    book_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True
    )
    from_state: Mapped[str] = mapped_column(String(50), nullable=False)
    to_state: Mapped[str] = mapped_column(String(50), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    chapter_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    step_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    __table_args__ = (UniqueConstraint("chapter_id", "state_version"),)


class ChapterVersion(Base, TimestampMixin):
    __tablename__ = "chapter_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    source_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_kind: Mapped[str | None] = mapped_column(Text, nullable=True)  # draft|patched|final|human_revision
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chapter_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    finalization_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # v9.3 editorial lineage (spec §31): v4 parent=v3, origin=editorial_review
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    editorial_review_round_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    revision_origin: Mapped[str | None] = mapped_column(String(40), nullable=True)  # editorial_revision|editorial_replan|human_direct_edit
    __table_args__ = (UniqueConstraint("chapter_id", "version"),)


class ChapterRun(Base, TimestampMixin):
    """One recoverable production run for a chapter (AI__.md v3.0 §4.1.2)."""
    __tablename__ = "chapter_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=False, index=True)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False)
    outline_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outline_versions.id"), nullable=False
    )
    pipeline_version: Mapped[str] = mapped_column(Text, nullable=False, default="pipeline-v2")
    status: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    current_step: Mapped[str | None] = mapped_column(Text, nullable=True)
    control_requested: Mapped[str] = mapped_column(Text, nullable=False, default="none")
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    resume_from_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    model_binding_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    budget_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    lease_owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="api")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # v9.4: owning writing session, NULL for manual single-run chapters
    writing_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    __table_args__ = (
        UniqueConstraint("chapter_id", "request_id"),
        Index("ix_chapter_runs_session_status", "writing_session_id", "status"),
    )


class ChapterStepRun(Base, TimestampMixin):
    """Step execution / checkpoint row (AI__.md v3.0 §4.1.4)."""
    __tablename__ = "chapter_step_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    chapter_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapter_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_name: Mapped[str] = mapped_column(Text, nullable=False)
    step_key: Mapped[str] = mapped_column(Text, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reused_from_step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("chapter_run_id", "step_key", "attempt_no"),)


class ChapterDispatchOutbox(Base, TimestampMixin):
    """Transactional outbox for chapter run dispatch (AI__.md v3.0 §4.1.5)."""
    __tablename__ = "chapter_dispatch_outbox"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    chapter_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapter_runs.id"), nullable=False
    )
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False, default="dispatch_chapter_run")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WritingSession(Base, TimestampMixin):
    """v9.4: one time-window autonomous writing session per book (spec §4.1)."""
    __tablename__ = "writing_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="duration")  # duration|until_time|manual
    requested_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="created", index=True)
    control_requested: Mapped[str] = mapped_column(String(15), nullable=False, default="none")  # none|pause|cancel
    current_chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    current_chapter_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_chapter_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    chapters_started: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    chapters_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    words_generated: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    create_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    stop_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    stop_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reconcile_lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reconcile_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # v9.5 model autopilot (spec §45)
    model_route_plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    model_routing_policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_preflight_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # pending|pass|blocked
    model_preflight_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    __table_args__ = (
        Index("ix_writing_sessions_book_key", "book_id", "create_idempotency_key"),
    )


class WritingSessionEvent(Base):
    """Immutable per-session event log with dedupe (spec §4.2)."""
    __tablename__ = "writing_session_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("writing_sessions.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("session_id", "dedupe_key"),)


class SessionAdvanceOutbox(Base, TimestampMixin):
    """Transactional outbox for session advancement (spec §4.3)."""
    __tablename__ = "session_advance_outbox"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    writing_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("writing_sessions.id"), nullable=False
    )
    completed_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, default="advance_writing_session")
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (Index("ix_sao_session_status", "writing_session_id", "status"),)


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
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
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
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
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
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
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
    chapter_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    step_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
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
    # v9.5 autopilot routing fields (spec §3)
    routing_mode: Mapped[str] = mapped_column(String(12), nullable=False, default="hybrid")  # manual|auto|hybrid
    routing_policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    manual_primary_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manual_fallback_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allowed_model_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    blocked_model_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")


class ModelCatalog(Base, TimestampMixin):
    """v9.5: provider/model registry (spec §6)."""
    __tablename__ = "model_catalog"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    provider: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    family: Mapped[str | None] = mapped_column(String(60), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(100), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auto_route_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    availability_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")  # available|missing|disabled
    discovery_source: Mapped[str] = mapped_column(String(30), nullable=False, default="seed")  # provider_api|manual|seed
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    __table_args__ = (UniqueConstraint("provider", "model_id"),)


class ModelCapabilityProfile(Base, TimestampMixin):
    """v9.5: per-model capability facts (spec §7)."""
    __tablename__ = "model_capability_profiles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    model_catalog_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_catalog.id"), nullable=False, index=True
    )
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supports_stream: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supports_json_schema: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_reasoning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_system_prompt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reasoning_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    capability_source: Mapped[str] = mapped_column(String(30), nullable=False, default="unknown")  # manual|provider_metadata|benchmark|inferred
    capability_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_tier: Mapped[str] = mapped_column(String(12), nullable=False, default="unknown")  # S|A|B|C|unknown
    static_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class ModelHealthProbe(Base, TimestampMixin):
    """v9.5: one probe attempt (spec §22)."""
    __tablename__ = "model_health_probes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    model_catalog_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_catalog.id"), nullable=False, index=True
    )
    probe_type: Mapped[str] = mapped_column(String(12), nullable=False)  # l0_provider|l1_ping|l2_capability
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)  # ok|failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    output_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detail_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class ModelHealthSnapshot(Base, TimestampMixin):
    """v9.5: one row per model health (spec §23)."""
    __tablename__ = "model_health_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    model_catalog_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_catalog.id"), nullable=False, unique=True
    )
    health_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", index=True)
    success_rate_15m: Mapped[float | None] = mapped_column(Float, nullable=True)
    success_rate_1h: Mapped[float | None] = mapped_column(Float, nullable=True)
    success_rate_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    p50_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    p95_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_mix_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    health_score: Mapped[float | None] = mapped_column(Float, nullable=True)


class ModelRoleScore(Base, TimestampMixin):
    """v9.5: role-wise model quality score (spec §11–§18)."""
    __tablename__ = "model_role_scores"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    model_catalog_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_catalog.id"), nullable=False, index=True
    )
    agent_role: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    static_prior_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    production_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    human_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    score_version: Mapped[str] = mapped_column(String(30), nullable=False, default="v1")
    detail_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    __table_args__ = (UniqueConstraint("model_catalog_id", "agent_role"),)


class ModelRoutingPolicy(Base, TimestampMixin):
    """v9.5: routing policy (spec §34)."""
    __tablename__ = "model_routing_policies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False, default="global")  # global|book
    scope_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    mode: Mapped[str] = mapped_column(String(12), nullable=False, default="hybrid")  # manual|auto|hybrid
    min_quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    min_health_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    require_provider_diversity: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fallback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    allow_degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quality_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.45)
    reliability_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.25)
    context_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.20)
    health_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.10)
    latency_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    cost_weight: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    role_overrides_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class ModelRoutePlan(Base, TimestampMixin):
    """v9.5: frozen per-session route assignment (spec §43)."""
    __tablename__ = "model_route_plans"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    writing_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("writing_sessions.id"), nullable=True, index=True
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignments_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    health_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active|superseded|failed


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
    prompt_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
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


# ---- v9.2 Style Intelligence Engine tables (spec §41, §52) ----
class StyleProfile(Base, TimestampMixin):
    """StyleProfile v2 — deterministic metrics + LLM semantic analysis (spec §41)."""
    __tablename__ = "style_profiles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")

    metric_vector: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    metric_ranges: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    fingerprint: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    narrative_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    dialogue_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    rhythm_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    emotion_expression_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    technique_profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    scene_mode_profiles: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    confidence_by_dimension: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")

    analyzer_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metric_engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChapterStyleScore(Base, TimestampMixin):
    """Per-chapter style score + drift distance (spec §52)."""
    __tablename__ = "chapter_style_scores"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False)
    surface_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    rhythm_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    dialogue_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    narrative_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    emotion_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    voice_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    overall_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    distance_to_profile: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    metric_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    __table_args__ = (
        UniqueConstraint("book_id", "chapter_no", name="uq_chapter_style_scores_book_chapter"),
    )


class StyleSampleSegment(Base, TimestampMixin):
    """A stratified text segment of a reference sample (spec §42).

    Original long text stays only in ReferenceSample; this stores the segment's
    deterministic metrics + optional semantic analysis, never the raw copy.
    """
    __tablename__ = "style_sample_segments"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    reference_sample_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reference_samples.id"), nullable=False, index=True
    )
    segment_no: Mapped[int] = mapped_column(Integer, nullable=False)
    scene_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_position_bucket: Mapped[str | None] = mapped_column(String(16), nullable=True)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    end_char: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metric_vector: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    semantic_analysis: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class SceneStyleContract(Base, TimestampMixin):
    """Per-scene style contract (spec §45): numeric targets + semantic guidance."""
    __tablename__ = "scene_style_contracts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    style_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("style_profiles.id"), nullable=True
    )
    scene_no: Mapped[int] = mapped_column(Integer, nullable=False)
    scene_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="general")
    targets: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    semantic: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    avoid: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    __table_args__ = (
        UniqueConstraint("book_id", "scene_no", name="uq_scene_style_contracts_book_scene"),
    )


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


# ---- NovelForge v8.0: library / import / prompt studio ----
class BookProfile(Base, TimestampMixin):
    __tablename__ = "book_profiles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, unique=True, index=True)
    logline: Mapped[str | None] = mapped_column(Text, nullable=True)
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True)
    genre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    themes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    tone: Mapped[str | None] = mapped_column(Text, nullable=True)
    audience: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_boundaries: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    core_loop: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class BookSource(Base, TimestampMixin):
    __tablename__ = "book_sources"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=True, index=True)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(200), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(50), default="v1", nullable=False)
    extracted_text_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_blocks_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    legacy_import: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ImportSession(Base, TimestampMixin):
    __tablename__ = "import_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="uploaded", index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("book_sources.id"), nullable=False, index=True)
    book_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=True, index=True)
    primary_document_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    document_types: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str] = mapped_column(String(50), default="v8.0", nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(50), default="v8.0", nullable=False)
    control_requested: Mapped[str | None] = mapped_column(String(50), nullable=True)
    preview_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    commit_idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImportSessionEvent(Base, TimestampMixin):
    __tablename__ = "import_session_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    import_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("import_sessions.id"), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_status: Mapped[str] = mapped_column(String(50), nullable=False)
    step: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class ImportArtifact(Base, TimestampMixin):
    __tablename__ = "import_artifacts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    import_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("import_sessions.id"), nullable=False, index=True)
    artifact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    artifact_key: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(50), default="ready", nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    __table_args__ = (UniqueConstraint("import_session_id", "artifact_key", "version"),)


class ImportConflict(Base, TimestampMixin):
    __tablename__ = "import_conflicts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    import_session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("import_sessions.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")  # warning|blocking
    entity_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    entity_temp_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    selected_option_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="open", nullable=False)  # open|resolved|ignored
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")


class LocationCard(Base, TimestampMixin):
    __tablename__ = "location_cards"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    environment: Mapped[str | None] = mapped_column(Text, nullable=True)
    resources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    rules: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    parent_location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)


class CharacterRelationship(Base, TimestampMixin):
    __tablename__ = "character_relationships"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    from_character_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    to_character_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    strength: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_chapter_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_chapter_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)


class OutlineVolume(Base, TimestampMixin):
    __tablename__ = "outline_volumes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    outline_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("outline_versions.id"), nullable=True, index=True)
    volume_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    chapter_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chapter_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    themes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    required_outcomes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    forbidden_outcomes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    involved_character_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    __table_args__ = (UniqueConstraint("book_id", "volume_no", "outline_version_id"),)


class WritingConstraint(Base, TimestampMixin):
    __tablename__ = "writing_constraints"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(50), nullable=False)  # book|volume|arc|chapter|character
    scope_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    constraint_type: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_hard: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class PromptTemplateVersion(Base, TimestampMixin):
    """v8 Prompt Studio versioned templates (does not replace legacy PromptTemplate seed)."""
    __tablename__ = "prompt_template_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    template_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    agent_role: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(50), default="system", nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)  # draft|active|archived
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    user_prompt_template: Mapped[str] = mapped_column(Text, nullable=False, default="")
    input_contract_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_contract_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    output_contract_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    output_contract_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    allowed_context_kinds: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    required_context_kinds: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    forbidden_context_kinds: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    required_model_capabilities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    default_temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    default_max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    variables: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    template_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    last_test_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    __table_args__ = (UniqueConstraint("template_key", "scope_type", "scope_id", "version"),)


class PromptTestRun(Base, TimestampMixin):
    __tablename__ = "prompt_test_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    template_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("prompt_template_versions.id"), nullable=False, index=True)
    fixture_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    output_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    contract_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    leak_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="completed", nullable=False)


# ---- v9.0 Cognitive-Causal Narrative Engine tables ----
class CharacterCoreAnchor(Base, TimestampMixin):
    """Long-term stable causal anchor of a character (not current emotion)."""
    __tablename__ = "character_core_anchors"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    character_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("character_cards.id"), nullable=False, index=True)
    anchor_code: Mapped[str] = mapped_column(String(32), nullable=False)
    anchor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[float] = mapped_column(Float, default=0.5)
    rigidity: Mapped[float] = mapped_column(Float, default=0.5)
    source_kind: Mapped[str] = mapped_column(String(32), default="auto")
    source_ref: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    __table_args__ = (
        UniqueConstraint("character_id", "anchor_code"),
        Index("idx_core_anchor_book_char", "book_id", "character_id"),
    )


class SceneReasoningContract(Base, TimestampMixin):
    """Formal agreement between Planner and DraftWriter for one scene (v9 CCNE)."""
    __tablename__ = "scene_reasoning_contracts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=False, index=True)
    scene_no: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="proposed", nullable=False)
    validation_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False, server_default="{}")
    __table_args__ = (
        UniqueConstraint("chapter_id", "scene_no", "contract_hash"),
        Index("idx_scene_contract_book_chapter", "book_id", "chapter_id"),
    )


class StoryEventEdge(Base, TimestampMixin):
    """Directed causal edge between finalized StoryEvents (v9 CCNE causal graph)."""
    __tablename__ = "story_event_edges"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=False, index=True)
    source_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("story_events.id"), nullable=False, index=True)
    target_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("story_events.id"), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    edge_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    mechanism: Mapped[str | None] = mapped_column(Text, nullable=True)
    strength: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_contract_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    __table_args__ = (
        Index("idx_story_event_edges_source", "book_id", "source_event_id"),
        Index("idx_story_event_edges_target", "book_id", "target_event_id"),
    )


# ---- v9.1 Research production tables (spec §17) ----
class ResearchSource(Base, TimestampMixin):
    """Registered scraping source with selector rules (spec §17.1)."""
    __tablename__ = "research_sources"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    chapter_list_selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_selector: Mapped[str] = mapped_column(Text, nullable=False)
    pagination_selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    encoding: Mapped[str] = mapped_column(String(32), nullable=False, default="utf-8", server_default="utf-8")
    rate_limit: Mapped[float] = mapped_column(Float, nullable=False, default=0.5, server_default="0.5")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False, default="experimental", server_default="experimental")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class ResearchTask(Base, TimestampMixin):
    """One scraping task executed by the ARQ worker (spec §17.2)."""
    __tablename__ = "research_tasks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=True, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("research_sources.id"), nullable=False, index=True)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    current_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchDocument(Base, TimestampMixin):
    """Full-text scraped document — content is ALWAYS persisted (spec §17.3, §23)."""
    __tablename__ = "research_documents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("research_tasks.id"), nullable=False, index=True)
    book_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=True, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    __table_args__ = (
        UniqueConstraint("task_id", "source_url", name="uq_research_documents_task_url"),
    )


class ResearchExport(Base, TimestampMixin):
    """Materialized export artifact (real files, spec §17.4)."""
    __tablename__ = "research_exports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("research_tasks.id"), nullable=False, index=True)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)


# ---- v9.2 Research source certification tables (spec §4, §5) ----
class ResearchSourceProbeRun(Base, TimestampMixin):
    """Evidence record of one source probe (spec §4)."""
    __tablename__ = "research_source_probe_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_sources.id"), nullable=False, index=True
    )
    source_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    test_url: Mapped[str] = mapped_column(Text, nullable=False)
    probe_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="generic")

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    title_hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    list_link_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    content_hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    extracted_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    anti_bot_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    encoding_detected: Mapped[str | None] = mapped_column(String(32), nullable=True)

    diagnostics_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class ResearchSourceVersion(Base, TimestampMixin):
    """Versioned rule configuration for a source (spec §5)."""
    __tablename__ = "research_source_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("research_sources.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="experimental")
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        UniqueConstraint("source_id", "version", name="uq_research_source_versions_source_version"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# v9.3 Tables: Editorial Learning Loop (ELL)
# ═══════════════════════════════════════════════════════════════════════════════

class EditorialReviewPolicy(Base, TimestampMixin):
    """Per-book human review policy (spec §5, §6)."""
    __tablename__ = "editorial_review_policies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=False, unique=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="windowed")  # blocking|windowed|learning_only
    max_unreviewed_ahead: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    review_sampling_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="all")  # all|risk_based|random|hybrid
    require_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    good_score_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=85)
    auto_pause_good_rate_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    auto_pause_consecutive_bad: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    rubric_template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    experience_auto_activation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    low_risk_auto_promote: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class EditorialRubricTemplate(Base, TimestampMixin):
    """Scoring rubric: weighted dimensions with anchored descriptions (spec §11)."""
    __tablename__ = "editorial_rubric_templates"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("books.id"), nullable=True, index=True)  # NULL = default template
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    dimensions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (UniqueConstraint("book_id", "name"),)


class EditorialReviewRound(Base, TimestampMixin):
    """One human review pass over a chapter version (spec §14)."""
    __tablename__ = "editorial_review_rounds"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=False, index=True)
    chapter_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chapter_versions.id"), nullable=False)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft")  # draft|submitted
    verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)  # accept|accept_with_notes|revise|reject
    score_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grade: Mapped[str | None] = mapped_column(String(2), nullable=True)  # A|B|C|D
    rubric_template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    rubric_scores_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    overall_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="human")
    reviewer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_issue_dispositions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # issue_id → confirmed|dismissed|corrected
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("chapter_id", "round_no"),)


class EditorialAnnotation(Base, TimestampMixin):
    """Human markup on chapter text with composite anchor (spec §15, §16)."""
    __tablename__ = "editorial_annotations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    review_round_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("editorial_review_rounds.id", ondelete="CASCADE"), nullable=False, index=True)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chapter_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    annotation_type: Mapped[str] = mapped_column(String(30), nullable=False)  # issue|suggestion|direct_edit|praise|question|preference|forbidden_pattern
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="minor")  # critical|major|minor|note|praise
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="local_span")  # local_span|scene|chapter|character|scene_type|book_future|global
    scene_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paragraph_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quoted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_issue_match_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    resolution_status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")  # open|resolved|unresolved|moved
    resolved_by_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class EditorialFeedbackInsight(Base, TimestampMixin):
    """Structured interpretation of one annotation (spec §42)."""
    __tablename__ = "editorial_feedback_insights"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    annotation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("editorial_annotations.id", ondelete="CASCADE"), nullable=False, unique=True)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    normalized_category: Mapped[str] = mapped_column(String(50), nullable=False)
    human_intent: Mapped[str | None] = mapped_column(Text, nullable=True)
    symptom: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause_component: Mapped[str] = mapped_column(String(50), nullable=False)  # chapter_planner|ccne|context|draft_writer|style|voice|review_agent|patch_editor|memory
    secondary_components: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    remediation_level: Mapped[str] = mapped_column(String(30), nullable=False, default="L1")  # L0..L5
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class EditorialExperienceCard(Base, TimestampMixin):
    """Editorial '错题本': generalized, retrievable experience (spec §33-§40)."""
    __tablename__ = "editorial_experience_cards"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)  # NULL = global
    rule_type: Mapped[str] = mapped_column(String(30), nullable=False)  # preference|anti_pattern|positive_pattern|character_rule|scene_mode_rule|review_rule|planning_rule|style_rule
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False, default="book")  # book|global|character|scene_type
    scope_ref: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trigger_conditions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    avoid_when: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    target_components: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    positive_example_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    negative_example_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    support_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    contradiction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="candidate")  # candidate|active|locked|superseded|rejected
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    effective_from_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_annotation_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class EditorialPreferencePair(Base, TimestampMixin):
    """Direct-edit supervision pair: rejected=AI, chosen=human (spec §17, §18)."""
    __tablename__ = "editorial_preference_pairs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    review_round_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    annotation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    context_package_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    scene_contract_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    style_contract_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rejected_text: Mapped[str] = mapped_column(Text, nullable=False)
    chosen_text: Mapped[str] = mapped_column(Text, nullable=False)
    preference_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False, default="local_span")
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="human_direct_edit")


class EditorialImprovementProposal(Base, TimestampMixin):
    """Candidate system change backed by evidence; never auto-applied (spec §43-§47, §80)."""
    __tablename__ = "editorial_improvement_proposals"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    proposal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_component: Mapped[str] = mapped_column(String(50), nullable=False)
    target_scope: Mapped[str] = mapped_column(String(20), nullable=False, default="book")
    current_version_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    candidate_patch: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False, default="low")  # low|medium|high
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    supporting_experience_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    supporting_review_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed")  # proposed|approved|experimenting|promoted|rolled_back|rejected
    created_by_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_from_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EditorialRegressionCase(Base, TimestampMixin):
    """Historical human-reviewed chapter as replayable test case (spec §48-§50)."""
    __tablename__ = "editorial_regression_cases"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    source_review_round_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    chapter_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scene_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    case_type: Mapped[str] = mapped_column(String(30), nullable=False, default="chapter_review")  # chapter_review|review_agent|draft_scene|planner
    target_component: Mapped[str] = mapped_column(String(50), nullable=False, default="review_agent")
    context_package_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    prompt_version_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model_binding_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    scene_contract_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    style_contract_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    chapter_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    human_verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)
    rubric_scores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    human_annotation_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    expected_properties: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    forbidden_properties: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    difficulty: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    scene_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class EditorialExperiment(Base, TimestampMixin):
    """Baseline vs candidate replay with hard gates (spec §51-§58, §81)."""
    __tablename__ = "editorial_experiments"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=gen_uuid)
    book_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    baseline_version: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_version: Mapped[str] = mapped_column(String(100), nullable=False)
    case_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    metrics_baseline: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    metrics_candidate: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    delta_metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    hard_gate_results: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    pareto_candidates: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")  # running|completed|failed
    recommendation: Mapped[str | None] = mapped_column(String(20), nullable=True)  # promote|hold|reject
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


