"""v9.7 system-closure tables (kept as a separate module to keep tables.py sane)."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow


class QualitySignal(Base, TimestampMixin):
    """v9.7 §14: unified quality feedback bus row."""
    __tablename__ = "quality_signals"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    chapter_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    signal_type: Mapped[str] = mapped_column(String(40), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(80), nullable=False)
    numeric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str | None] = mapped_column(String(60), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (
        Index("ix_quality_signals_book_role", "book_id", "agent_role", "created_at"),
        Index("ix_quality_signals_book_metric", "book_id", "metric_name", "created_at"),
    )


class PromptEvolutionRun(Base, TimestampMixin):
    __tablename__ = "prompt_evolution_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    target_role: Mapped[str] = mapped_column(String(100), nullable=False)
    trigger_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    trigger_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    winner_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class PromptEvolutionCandidate(Base, TimestampMixin):
    __tablename__ = "prompt_evolution_candidates"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    candidate_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_prompt_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_policy_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class TechniqueCardUsage(Base, TimestampMixin):
    __tablename__ = "technique_card_usages"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    technique_card_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scene_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class AIToneFinding(Base, TimestampMixin):
    """v9.7 §25: diagnostic finding only — never auto-rewrites the chapter."""
    __tablename__ = "ai_tone_findings"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    chapter_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    paragraph_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
    start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_id: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="minor")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    scene_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    style_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_patchable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    human_disposition: Mapped[str | None] = mapped_column(String(16), nullable=True)
    corrected_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    __table_args__ = (Index("ix_ai_tone_findings_book_chapter", "book_id", "chapter_id", "rule_id"),)


class AIToneRuleCalibration(Base, TimestampMixin):
    __tablename__ = "ai_tone_rule_calibrations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    book_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    rule_id: Mapped[str] = mapped_column(String(60), nullable=False)
    confirmed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dismissed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    corrected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)


class ModelEvalSuite(Base, TimestampMixin):
    __tablename__ = "model_eval_suites"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    suite_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pass_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ModelEvalCase(Base, TimestampMixin):
    __tablename__ = "model_eval_cases"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    suite_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    case_key: Mapped[str] = mapped_column(String(120), nullable=False)
    case_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1")
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    context_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    generator_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    generator_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    expected_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_schema: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    grader_type: Mapped[str] = mapped_column(String(30), nullable=False)
    grader_config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=1024)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    private_case: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    case_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ModelEvalRun(Base, TimestampMixin):
    __tablename__ = "model_eval_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_catalog_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="qualification")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    benchmark_revision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    __table_args__ = (Index("ix_model_eval_runs_model_status", "model_catalog_id", "status"),)


class ModelEvalCaseResult(Base, TimestampMixin):
    __tablename__ = "model_eval_case_results"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    variant_seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    context_target_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    grader_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_per_second: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)


class ModelContextProfile(Base, TimestampMixin):
    __tablename__ = "model_context_profiles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_catalog_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    declared_context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accepted_context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_robustness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    multi_hop_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    instruction_retention_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    belief_boundary_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    benchmark_revision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
