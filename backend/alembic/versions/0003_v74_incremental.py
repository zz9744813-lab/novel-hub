"""v7.4 Alembic migration: model bindings + change log + route events + context packages + references + genre profiles + research."""
"""Model bindings, change log, route events, context packages, references, genre profiles, research sessions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_v74_incremental"
down_revision = "0002_missing_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 3.1 Agent model bindings
    op.create_table(
        "agent_model_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_type", sa.Text, nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_role", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("primary_model", sa.Text, nullable=False),
        sa.Column("fallback_model", sa.Text, nullable=True),
        sa.Column("reasoning_mode", sa.Text, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("updated_by", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "(scope_type = 'global' AND scope_id IS NULL) OR (scope_type = 'book' AND scope_id IS NOT NULL)",
            "ck_scope_match",
        ),
        sa.CheckConstraint(
            "reasoning_mode IN ('auto', 'enabled', 'disabled', 'disabled_if_supported')",
            "ck_reasoning_mode",
        ),
    )
    op.execute("""
        CREATE UNIQUE INDEX ux_agent_model_binding
        ON agent_model_bindings (
            scope_type,
            COALESCE(scope_id, '00000000-0000-0000-0000-000000000000'::uuid),
            agent_role
        )
    """)

    # 3.2 Model change log
    op.create_table(
        "model_change_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("binding_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_model_bindings.id"), nullable=False),
        sa.Column("agent_role", sa.Text, nullable=False),
        sa.Column("old_provider", sa.Text, nullable=True),
        sa.Column("old_model", sa.Text, nullable=True),
        sa.Column("new_provider", sa.Text, nullable=False),
        sa.Column("new_model", sa.Text, nullable=False),
        sa.Column("old_reasoning_mode", sa.Text, nullable=True),
        sa.Column("new_reasoning_mode", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("changed_by", sa.Text, nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # 3.3 Model route events
    op.create_table(
        "model_route_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer, nullable=False),
        sa.Column("agent_role", sa.Text, nullable=False),
        sa.Column("configured_provider", sa.Text, nullable=False),
        sa.Column("configured_model", sa.Text, nullable=False),
        sa.Column("actual_provider", sa.Text, nullable=False),
        sa.Column("actual_model", sa.Text, nullable=False),
        sa.Column("route_type", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("route_type IN ('primary', 'retry', 'fallback')", "ck_route_type"),
        sa.UniqueConstraint("run_id", "attempt_no", name="uq_route_run_attempt"),
    )

    # 3.4 Reference samples
    op.create_table(
        "reference_samples",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("original_filename", sa.Text, nullable=False),
        sa.Column("storage_path", sa.Text, nullable=False),
        sa.Column("content_sha256", sa.Text, nullable=False),
        sa.Column("mime_type", sa.Text, nullable=False),
        sa.Column("original_size_bytes", sa.BigInteger, nullable=False),
        sa.Column("compressed_size_bytes", sa.BigInteger, nullable=False),
        sa.Column("character_count", sa.Integer, nullable=False),
        sa.Column("genre_hint", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('uploaded', 'extracting', 'ready', 'analyzing', 'analyzed', 'failed', 'deleted')",
            "ck_ref_status",
        ),
        sa.UniqueConstraint("book_id", "content_sha256", name="uq_ref_book_sha"),
    )

    # 3.5 Genre profiles
    op.create_table(
        "genre_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("narrative_person", sa.Text, nullable=True),
        sa.Column("pacing_profile", postgresql.JSONB, nullable=False),
        sa.Column("technique_tags", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"),
        sa.Column("lexical_tendency", postgresql.JSONB, nullable=False),
        sa.Column("content_intensity_notes", sa.Text, nullable=True),
        sa.Column("prompt_injection_snippet", sa.Text, nullable=False),
        sa.Column("analyzer_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("sanitizer_report", postgresql.JSONB, nullable=False),
        sa.Column("approved_by", sa.Text, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_approval', 'approved', 'rejected', 'superseded')",
            "ck_genre_status",
        ),
        sa.UniqueConstraint("book_id", "version", name="uq_genre_book_version"),
    )
    op.create_index(
        "ux_genre_profile_active",
        "genre_profiles",
        ["book_id"],
        unique=True,
        postgresql_where=sa.text("status = 'approved'"),
    )

    # 3.6 Genre profile sources
    op.create_table(
        "genre_profile_sources",
        sa.Column("genre_profile_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("genre_profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("reference_sample_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("reference_samples.id"), primary_key=True),
    )

    # 3.7 Research sessions
    op.create_table(
        "research_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chapters.id"), nullable=True),
        sa.Column("outline_node_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("outline_nodes.id"), nullable=True),
        sa.Column("trigger_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("requested_topic", sa.Text, nullable=False),
        sa.Column("plan_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("synthesis_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=True),
        sa.Column("approved_by", sa.Text, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "trigger_type IN ('outline_suggestion', 'human_request')",
            "ck_research_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('suggested', 'approved', 'queued', 'planning', 'searching', 'synthesizing', 'completed', 'failed', 'cancelled')",
            "ck_research_status",
        ),
    )

    # 3.8 External research evidence
    op.create_table(
        "external_research_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("research_session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("research_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chapters.id"), nullable=True),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("source_domain", sa.Text, nullable=False),
        sa.Column("source_title", sa.Text, nullable=True),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("source_content_hash", sa.Text, nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trust_tier", sa.Text, nullable=False),
        sa.Column("relevance", sa.Text, nullable=False),
        sa.Column("confidence", sa.Numeric, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("conflicts_or_uncertainty", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("evidence_source", sa.Text, nullable=False, server_default="external_research"),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", "ck_confidence_range"),
        sa.CheckConstraint(
            "trust_tier IN ('primary', 'authoritative_secondary', 'general_secondary', 'unknown')",
            "ck_trust_tier",
        ),
        sa.CheckConstraint("relevance IN ('critical', 'high', 'medium', 'low')", "ck_relevance"),
        sa.CheckConstraint("status IN ('pending', 'approved', 'rejected', 'stale', 'superseded')", "ck_evidence_status"),
    )
    op.create_index("ix_external_research_book", "external_research_evidence", ["book_id", "status", sa.text("fetched_at DESC")])

    # 3.9 Agent context packages
    op.create_table(
        "agent_context_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer, nullable=False),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chapters.id"), nullable=True),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scenes.id"), nullable=True),
        sa.Column("agent_role", sa.Text, nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("prompt_template_hash", sa.Text, nullable=False),
        sa.Column("context_schema_version", sa.Text, nullable=False),
        sa.Column("assembler_version", sa.Text, nullable=False),
        sa.Column("request_parameters", postgresql.JSONB, nullable=False),
        sa.Column("assembly_manifest", postgresql.JSONB, nullable=False),
        sa.Column("l4_entity_refs", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("l1_ledger_refs", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False, server_default="{}"),
        sa.Column("l2_summary_refs", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False, server_default="{}"),
        sa.Column("l3_summary_refs", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False, server_default="{}"),
        sa.Column("genre_profile_ref", postgresql.UUID(as_uuid=True), sa.ForeignKey("genre_profiles.id"), nullable=True),
        sa.Column("story_evidence_refs", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False, server_default="{}"),
        sa.Column("external_evidence_refs", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=False, server_default="{}"),
        sa.Column("assembled_token_estimate", sa.Integer, nullable=False),
        sa.Column("rendered_prompt_hash", sa.Text, nullable=False),
        sa.Column("publish_state", sa.Text, nullable=False),
        sa.Column("block_reason", sa.Text, nullable=True),
        sa.Column("assembled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "attempt_no", name="uq_ctx_pkg_run_attempt"),
    )
    op.create_index("ix_ctx_pkg_chapter", "agent_context_packages", ["chapter_id", "agent_role", sa.text("assembled_at DESC")])


def downgrade() -> None:
    op.drop_index("ix_ctx_pkg_chapter", "agent_context_packages")
    op.drop_table("agent_context_packages")
    op.drop_index("ix_external_research_book", "external_research_evidence")
    op.drop_table("external_research_evidence")
    op.drop_table("research_sessions")
    op.drop_table("genre_profile_sources")
    op.drop_index("ux_genre_profile_active", "genre_profiles")
    op.drop_table("genre_profiles")
    op.drop_table("reference_samples")
    op.drop_table("model_route_events")
    op.drop_table("model_change_log")
    op.drop_index("ux_agent_model_binding", "agent_model_bindings")
    op.drop_table("agent_model_bindings")
