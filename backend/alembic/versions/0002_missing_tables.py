"""Add missing tables - character_cards, world_rules, plot_threads, etc.

Revision ID: 0002_missing_tables
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_missing_tables"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # character_cards
    op.create_table("character_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("role", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("card_json", postgresql.JSONB, default={}),
        sa.Column("version", sa.Integer, nullable=False, default=1),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("character_state_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("character_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True)),
        sa.Column("field", sa.Text, nullable=False),
        sa.Column("old_value", postgresql.JSONB),
        sa.Column("new_value", postgresql.JSONB),
        sa.Column("event_source", sa.Text),
        sa.Column("certainty", sa.Text, default="explicit"),
        sa.Column("evidence", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("character_state_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("character_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("as_of_chapter", sa.Integer, nullable=False),
        sa.Column("state", postgresql.JSONB, default={}),
        sa.Column("version", sa.Integer, nullable=False, default=1),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("world_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_key", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("rule_json", postgresql.JSONB, default={}),
        sa.Column("version", sa.Integer, nullable=False, default=1),
    )

    op.create_table("plot_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("planted_chapter", sa.Integer),
        sa.Column("resolved_chapter", sa.Integer),
        sa.Column("status", sa.Text, default="open"),
        sa.Column("description", sa.Text),
        sa.Column("thread_json", postgresql.JSONB, default={}),
    )

    op.create_table("relationship_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("character_a_id", postgresql.UUID(as_uuid=True)),
        sa.Column("character_b_id", postgresql.UUID(as_uuid=True)),
        sa.Column("relationship_type", sa.Text),
        sa.Column("change_type", sa.Text),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True)),
        sa.Column("evidence", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("item_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True)),
        sa.Column("holder_id", postgresql.UUID(as_uuid=True)),
        sa.Column("event_type", sa.Text),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True)),
        sa.Column("evidence", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("timeline_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True)),
        sa.Column("world_time_text", sa.Text),
        sa.Column("event_description", sa.Text),
        sa.Column("ordering", sa.Integer, nullable=False, default=0),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("entity_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.Text),
        sa.Column("alias", sa.Text, nullable=False),
        sa.Column("is_primary", sa.Boolean, default=False),
    )
    op.create_index("idx_entity_aliases_trgm", "entity_aliases", ["alias"], postgresql_using="gin", postgresql_ops={"alias": "gin_trgm_ops"})

    op.create_table("style_voice_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("character_id", postgresql.UUID(as_uuid=True)),
        sa.Column("register", sa.Text),
        sa.Column("sentence_patterns", postgresql.JSONB, default=[]),
        sa.Column("vocabulary_preferences", postgresql.JSONB, default=[]),
        sa.Column("emotion_expression", sa.Text),
        sa.Column("taboo_phrases", postgresql.JSONB, default=[]),
        sa.Column("approved_examples", postgresql.JSONB, default=[]),
        sa.Column("version", sa.Integer, nullable=False, default=1),
    )

    op.create_table("style_tone_anchors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("narrative_pov", sa.Text),
        sa.Column("narrative_distance", sa.Text),
        sa.Column("emotional_temperature", sa.Text),
        sa.Column("imagery_density", sa.Text),
        sa.Column("description_intensity", sa.Text),
        sa.Column("pacing", sa.Text),
        sa.Column("humor_level", sa.Text),
        sa.Column("psychology_ratio", sa.Float),
        sa.Column("dialogue_narration_ratio", sa.Float),
        sa.Column("adult_content_policy", sa.Text),
        sa.Column("forbidden_expressions", postgresql.JSONB, default=[]),
        sa.Column("approved_samples", postgresql.JSONB, default=[]),
        sa.Column("version", sa.Integer, nullable=False, default=1),
    )

    op.create_table("query_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True)),
        sa.Column("plan_json", postgresql.JSONB, nullable=False),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("prompt_version", sa.Text),
        sa.Column("model_name", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("retrieval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("query_plan_id", postgresql.UUID(as_uuid=True)),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("degraded", sa.Boolean, default=False),
        sa.Column("candidate_count", sa.Integer, default=0),
        sa.Column("selected_count", sa.Integer, default=0),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("retrieval_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("retrieval_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("source_type", sa.Text),
        sa.Column("source_chapter", sa.Integer),
        sa.Column("source_scene", sa.Integer),
        sa.Column("scene_summary", sa.Text),
        sa.Column("evidence_excerpt", sa.Text),
        sa.Column("rule_score", sa.Integer, default=0),
        sa.Column("full_text_rank", sa.Float),
        sa.Column("llm_rank", sa.Integer),
        sa.Column("forced", sa.Boolean, default=False),
    )

    op.create_table("retrieval_judgements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("retrieval_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True)),
        sa.Column("relevance", sa.Text),
        sa.Column("reason", sa.Text),
        sa.Column("use_mode", sa.Text),
    )

    op.create_table("review_issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True)),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True)),
        sa.Column("paragraph_id", sa.Text),
        sa.Column("issue_cluster_id", postgresql.UUID(as_uuid=True)),
        sa.Column("issue_type", sa.Text),
        sa.Column("severity", sa.Text),
        sa.Column("evidence", sa.Text),
        sa.Column("authority_ref", sa.Text),
        sa.Column("repair_instruction", sa.Text),
        sa.Column("protected_facts", postgresql.JSONB, default=[]),
        sa.Column("status", sa.Text, default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("rewrite_patches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("issue_id", postgresql.UUID(as_uuid=True)),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True)),
        sa.Column("chapter_version", sa.Integer),
        sa.Column("scene_id", postgresql.UUID(as_uuid=True)),
        sa.Column("paragraph_id", sa.Text),
        sa.Column("start_offset", sa.Integer),
        sa.Column("end_offset", sa.Integer),
        sa.Column("expected_hash", sa.Text),
        sa.Column("replacement_text", sa.Text),
        sa.Column("reason", sa.Text),
        sa.Column("status", sa.Text, default="pending"),
        sa.Column("retry_round", sa.Integer, default=1),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("drift_audit_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_range_start", sa.Integer, nullable=False),
        sa.Column("chapter_range_end", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, default="pending"),
        sa.Column("metrics", postgresql.JSONB, default={}),
        sa.Column("redline_findings", postgresql.JSONB, default=[]),
        sa.Column("yellow_findings", postgresql.JSONB, default=[]),
        sa.Column("affected_entities", postgresql.JSONB, default=[]),
        sa.Column("affected_future_nodes", postgresql.JSONB, default=[]),
        sa.Column("recommended_actions", postgresql.JSONB, default=[]),
        sa.Column("evidence_refs", postgresql.JSONB, default=[]),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("llm_usage_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True)),
        sa.Column("run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("provider", sa.Text),
        sa.Column("model_name", sa.Text),
        sa.Column("prompt_tokens", sa.Integer, default=0),
        sa.Column("completion_tokens", sa.Integer, default=0),
        sa.Column("reasoning_tokens", sa.Integer, default=0),
        sa.Column("total_tokens", sa.Integer, default=0),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("human_interventions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("intervention_type", sa.Text),
        sa.Column("target_entity_type", sa.Text),
        sa.Column("target_entity_id", postgresql.UUID(as_uuid=True)),
        sa.Column("old_value", postgresql.JSONB),
        sa.Column("new_value", postgresql.JSONB),
        sa.Column("reason", sa.Text),
        sa.Column("status", sa.Text, default="applied"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table("technique_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("genre_tags", postgresql.JSONB, default=[]),
        sa.Column("pattern", sa.Text),
        sa.Column("applicable_conditions", postgresql.JSONB, default=[]),
        sa.Column("contraindications", postgresql.JSONB, default=[]),
        sa.Column("approved_by_human", sa.Boolean, default=False),
        sa.Column("source_refs", postgresql.JSONB, default=[]),
        sa.Column("version", sa.Integer, default=1),
    )

    # Add tsvector column to scene_search_documents if not exists
    op.execute("ALTER TABLE scene_search_documents ADD COLUMN IF NOT EXISTS search_tsv tsvector")


def downgrade() -> None:
    op.drop_table("technique_cards")
    op.drop_table("human_interventions")
    op.drop_table("llm_usage_events")
    op.drop_table("drift_audit_reports")
    op.drop_table("rewrite_patches")
    op.drop_table("review_issues")
    op.drop_table("retrieval_judgements")
    op.drop_table("retrieval_candidates")
    op.drop_table("retrieval_runs")
    op.drop_table("query_plans")
    op.drop_table("style_tone_anchors")
    op.drop_table("style_voice_cards")
    op.drop_table("entity_aliases")
    op.drop_table("timeline_events")
    op.drop_table("item_events")
    op.drop_table("relationship_events")
    op.drop_table("plot_threads")
    op.drop_table("world_rules")
    op.drop_table("character_state_snapshots")
    op.drop_table("character_state_events")
    op.drop_table("character_cards")
