"""Initial migration - all tables + extensions + indexes.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Books
    op.create_table("books",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="created"),
        sa.Column("target_chapters", sa.Integer, server_default="500"),
        sa.Column("target_words", sa.Integer, server_default="5000000"),
        sa.Column("finalized_chapters", sa.Integer, server_default="0"),
        sa.Column("finalized_words", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # Book settings
    op.create_table("book_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("book_id", "key"),
    )

    # Continue with core tables (abbreviated - full set in models)
    op.create_table("outline_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(50), nullable=False, server_default="upload"),
        sa.Column("raw_outline", sa.Text, nullable=True),
        sa.Column("parsed_json", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("book_id", "version"),
    )

    op.create_table("outline_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outline_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_type", sa.String(50), nullable=False),
        sa.Column("volume_no", sa.Integer, nullable=False, server_default="1"),
        sa.Column("chapter_no", sa.Integer, nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("goal", sa.Text, nullable=False),
        sa.Column("required_beats", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("forbidden_outcomes", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("involved_character_ids", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("plot_thread_ids", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("depends_on", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("expected_state_changes", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("book_id", "outline_version_id", "chapter_no"),
    )
    op.create_index("idx_outline_nodes_book_chapter", "outline_nodes", ["book_id", "chapter_no"])

    op.create_table("outline_dependencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outline_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dependency_type", sa.String(100), nullable=False),
        sa.Column("required", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("required_state", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table("chapter_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_no", sa.Integer, nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="queued"),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("priority", sa.Integer, server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_chapter_tasks_status", "chapter_tasks", ["status"])

    op.create_table("chapters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_no", sa.Integer, nullable=False),
        sa.Column("outline_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="queued"),
        sa.Column("finalized_version", sa.Integer, nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("book_id", "chapter_no"),
    )

    op.create_table("chapter_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("word_count", sa.Integer, server_default="0"),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("chapter_id", "version"),
    )

    op.create_table("scenes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_no", sa.Integer, nullable=False),
        sa.Column("pov_character_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("outline_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("canon_status", sa.String(50), server_default="draft", nullable=False),
        sa.Column("version", sa.Integer, server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("chapter_id", "scene_no", "version"),
    )

    op.create_table("paragraphs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paragraph_key", sa.String(50), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer, server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("scene_id", "paragraph_key", "version"),
    )

    # Story events
    op.create_table("story_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(200), nullable=False),
        sa.Column("subject_entity_ids", postgresql.JSONB, server_default=sa.text("'[]'")),
        sa.Column("object_entity_ids", postgresql.JSONB, server_default=sa.text("'[]'")),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plot_thread_ids", postgresql.JSONB, server_default=sa.text("'[]'")),
        sa.Column("before_state", postgresql.JSONB, nullable=True),
        sa.Column("after_state", postgresql.JSONB, nullable=True),
        sa.Column("cause_text", sa.Text, nullable=True),
        sa.Column("world_time_text", sa.String(500), nullable=True),
        sa.Column("evidence_paragraph_keys", postgresql.JSONB, server_default=sa.text("'[]'")),
        sa.Column("evidence_excerpt", sa.Text, nullable=False),
        sa.Column("certainty", sa.String(50), server_default="explicit", nullable=False),
        sa.Column("canon_status", sa.String(50), server_default="canon", nullable=False),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer, server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_story_events_book_chapter", "story_events", ["book_id", "chapter_id"])

    # Scene search documents with tsvector
    op.create_table("scene_search_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_no", sa.Integer, nullable=False),
        sa.Column("scene_no", sa.Integer, nullable=False),
        sa.Column("outline_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pov_character_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("character_ids", postgresql.JSONB, server_default=sa.text("'[]'")),
        sa.Column("location_ids", postgresql.JSONB, server_default=sa.text("'[]'")),
        sa.Column("item_ids", postgresql.JSONB, server_default=sa.text("'[]'")),
        sa.Column("plot_thread_ids", postgresql.JSONB, server_default=sa.text("'[]'")),
        sa.Column("event_types", postgresql.JSONB, server_default=sa.text("'[]'")),
        sa.Column("scene_summary", sa.Text, nullable=False),
        sa.Column("evidence_excerpt", sa.Text, nullable=False),
        sa.Column("search_text", sa.Text, nullable=False),
        sa.Column("search_tsv", sa.Text, server_default="", nullable=False),
        sa.Column("canon_status", sa.String(50), server_default="canon", nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer, server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("scene_id", "content_hash", "version"),
    )
    op.create_index("idx_scene_search_book", "scene_search_documents", ["book_id", "canon_status", "chapter_no"])

    # Memory tables - simplified for migration
    for table_name, extra_cols in [
        ("memory_l1_chapter_ledgers", [
            sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("finalized_version", sa.Integer, nullable=False),
            sa.Column("source_hash", sa.String(128), nullable=False),
            sa.Column("status", sa.String(50), server_default="generated", nullable=False),
            sa.Column("ledger_json", postgresql.JSONB, server_default=sa.text("'{}'")),
            sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        ]),
        ("memory_l2_stage_summaries", [
            sa.Column("chapter_range_start", sa.Integer, nullable=False),
            sa.Column("chapter_range_end", sa.Integer, nullable=False),
            sa.Column("outline_version", sa.Integer, nullable=False),
            sa.Column("source_hash", sa.String(128), nullable=False),
            sa.Column("status", sa.String(50), server_default="generated", nullable=False),
            sa.Column("summary_json", postgresql.JSONB, server_default=sa.text("'{}'")),
            sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        ]),
        ("memory_l3_volume_summaries", [
            sa.Column("volume_no", sa.Integer, nullable=False),
            sa.Column("outline_version", sa.Integer, nullable=False),
            sa.Column("source_hash", sa.String(128), nullable=False),
            sa.Column("status", sa.String(50), server_default="generated", nullable=False),
            sa.Column("summary_json", postgresql.JSONB, server_default=sa.text("'{}'")),
            sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        ]),
        ("memory_l4_state_snapshots", [
            sa.Column("entity_type", sa.String(100), nullable=False),
            sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("as_of_chapter", sa.Integer, nullable=False),
            sa.Column("state", postgresql.JSONB, server_default=sa.text("'{}'")),
            sa.Column("version", sa.Integer, server_default="1", nullable=False),
            sa.Column("status", sa.String(50), server_default="verified", nullable=False),
            sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("is_locked", sa.Boolean, server_default=sa.text("false"), nullable=False),
        ]),
    ]:
        op.create_table(table_name,
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
            *extra_cols,
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        )

    # Agent runs
    op.create_table("agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_role", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), server_default="running", nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(500), server_default="", nullable=False),
        sa.Column("parent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table("agent_run_outputs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_role", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(200), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("raw_provider_response", postgresql.JSONB, nullable=True),
        sa.Column("reasoning_text", sa.Text, nullable=True),
        sa.Column("final_content", sa.Text, nullable=True),
        sa.Column("normalized_content", sa.Text, nullable=True),
        sa.Column("publishable_content", sa.Text, nullable=True),
        sa.Column("reasoning_detected", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("inline_leak_detected", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("leak_status", sa.String(50), server_default="unchecked", nullable=False),
        sa.Column("output_integrity", sa.String(50), server_default="pending", nullable=False),
        sa.Column("parser_version", sa.String(50), server_default="v1", nullable=False),
        sa.Column("guard_version", sa.String(50), server_default="v1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # Prompt templates
    op.create_table("prompt_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_role", sa.String(100), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("system_prompt", sa.Text, nullable=False),
        sa.Column("input_variables", postgresql.JSONB, server_default=sa.text("'[]'")),
        sa.Column("output_schema", postgresql.JSONB, nullable=True),
        sa.Column("is_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("agent_role", "version"),
    )

    # Simplified remaining tables (drift_audit_reports, review_issues, rewrite_patches, etc.)
    # These will be created via SQLAlchemy auto-detection on first alembic autogenerate
    # For now, the core tables above are sufficient for Phase 1-2


def downgrade() -> None:
    # Drop all tables in reverse
    for table in [
        "prompt_templates", "agent_run_outputs", "agent_runs",
        "memory_l4_state_snapshots", "memory_l3_volume_summaries",
        "memory_l2_stage_summaries", "memory_l1_chapter_ledgers",
        "scene_search_documents", "story_events",
        "paragraphs", "scenes", "chapter_versions", "chapters", "chapter_tasks",
        "outline_dependencies", "outline_nodes", "outline_versions",
        "book_settings", "books",
    ]:
        op.drop_table_if_exists(table) if hasattr(op, "drop_table_if_exists") else op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
