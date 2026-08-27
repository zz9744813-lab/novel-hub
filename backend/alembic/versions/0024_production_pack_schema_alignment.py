"""Align legacy tables with the production-pack ORM writes.

Revision ID: 0024_production_schema
Revises: 0023_manuscript_release
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0024_production_schema"
down_revision = "0023_manuscript_release"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table)
    }


def _add_timestamps(table: str) -> None:
    columns = _columns(table)
    for name in ("created_at", "updated_at"):
        if name not in columns:
            op.add_column(
                table,
                sa.Column(
                    name,
                    sa.DateTime(timezone=True),
                    nullable=False,
                    server_default=sa.func.now(),
                ),
            )


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def _drop_columns_if_present(table: str, *names: str) -> None:
    columns = _columns(table)
    for name in names:
        if name in columns:
            op.drop_column(table, name)
            columns.remove(name)


def _rename_or_add(
    table: str,
    *,
    old: str,
    new: str,
    type_: sa.types.TypeEngine,
    nullable: bool = True,
    server_default=None,
) -> None:
    columns = _columns(table)
    if new in columns:
        return
    if old in columns:
        op.alter_column(table, old, new_column_name=new)
        return
    op.add_column(
        table,
        sa.Column(
            new,
            type_,
            nullable=nullable,
            server_default=server_default,
        ),
    )


def upgrade() -> None:
    _add_column_if_missing(
        "agent_run_outputs",
        sa.Column(
            "experience_refs",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    _add_column_if_missing(
        "agent_run_outputs",
        sa.Column(
            "technique_refs",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    for name, type_ in (
        ("proposal_id", UUID(as_uuid=True)),
        ("experiment_id", UUID(as_uuid=True)),
        ("canary_status", sa.String(20)),
        ("rolled_back_from_id", UUID(as_uuid=True)),
    ):
        _add_column_if_missing(
            "chapter_versions",
            sa.Column(name, type_, nullable=True),
        )

    _add_column_if_missing(
        "character_state_events",
        sa.Column("source_run_id", UUID(as_uuid=True), nullable=True),
    )
    _add_timestamps("character_state_events")
    _add_column_if_missing(
        "character_state_snapshots",
        sa.Column(
            "source_run_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default="00000000-0000-0000-0000-000000000000",
        ),
    )
    _add_column_if_missing(
        "character_state_snapshots",
        sa.Column(
            "is_locked", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    _add_timestamps("character_state_snapshots")

    for table in (
        "editorial_review_policies",
        "editorial_rubric_templates",
        "entity_aliases",
    ):
        _add_timestamps(table)

    for name, type_ in (
        ("source_kind", sa.String(30)),
        ("source_ref_json", JSONB()),
        ("source_url", sa.Text()),
        ("research_task_id", UUID(as_uuid=True)),
        ("research_document_id", UUID(as_uuid=True)),
        ("imported_at", sa.DateTime(timezone=True)),
    ):
        _add_column_if_missing(
            "genre_profiles", sa.Column(name, type_, nullable=True)
        )

    _add_column_if_missing(
        "human_interventions",
        sa.Column(
            "resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    _add_timestamps("human_interventions")

    _add_column_if_missing(
        "item_events",
        sa.Column("character_id", UUID(as_uuid=True), nullable=True),
    )
    if "description" not in _columns("item_events"):
        op.add_column(
            "item_events",
            sa.Column("description", sa.Text(), nullable=True),
        )
        op.execute(
            "UPDATE item_events SET description = COALESCE(evidence, '') "
            "WHERE description IS NULL"
        )
        op.alter_column("item_events", "description", nullable=False)
    _add_timestamps("item_events")

    _add_column_if_missing(
        "llm_usage_events", sa.Column("cost_usd", sa.Float(), nullable=True)
    )
    _add_timestamps("llm_usage_events")
    _add_timestamps("query_plans")

    _add_column_if_missing(
        "relationship_events",
        sa.Column("old_state", sa.String(200), nullable=True),
    )
    if "new_state" not in _columns("relationship_events"):
        op.add_column(
            "relationship_events",
            sa.Column("new_state", sa.String(200), nullable=True),
        )
        op.execute(
            "UPDATE relationship_events "
            "SET new_state = COALESCE(change_type, relationship_type, '') "
            "WHERE new_state IS NULL"
        )
        op.alter_column("relationship_events", "new_state", nullable=False)
    _add_timestamps("relationship_events")

    for table in (
        "retrieval_candidates",
        "retrieval_judgements",
        "retrieval_runs",
    ):
        _add_timestamps(table)

    _add_column_if_missing(
        "review_issues",
        sa.Column(
            "resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    _add_timestamps("review_issues")

    for name in ("preserved_before", "preserved_after"):
        _add_column_if_missing(
            "rewrite_patches", sa.Column(name, sa.Text(), nullable=True)
        )
    _add_column_if_missing(
        "rewrite_patches",
        sa.Column(
            "resolved_issue_ids",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    _add_column_if_missing(
        "rewrite_patches",
        sa.Column(
            "source_run_id",
            UUID(as_uuid=True),
            nullable=False,
            server_default="00000000-0000-0000-0000-000000000000",
        ),
    )
    _add_timestamps("rewrite_patches")

    if "description" not in _columns("timeline_events"):
        op.add_column(
            "timeline_events",
            sa.Column("description", sa.Text(), nullable=True),
        )
        op.execute(
            "UPDATE timeline_events SET description = COALESCE(event_description, '') "
            "WHERE description IS NULL"
        )
        op.alter_column("timeline_events", "description", nullable=False)
    _add_timestamps("timeline_events")

    technique_columns = _columns("technique_cards")
    if "book_id" not in technique_columns:
        op.add_column(
            "technique_cards",
            sa.Column(
                "book_id",
                UUID(as_uuid=True),
                sa.ForeignKey("books.id"),
                nullable=True,
            ),
        )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_technique_cards_book_status "
        "ON technique_cards (book_id, status, technique_type)"
    )
    _add_timestamps("technique_cards")

    plot_columns = _columns("plot_threads")
    if "version" not in plot_columns:
        op.add_column(
            "plot_threads",
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        )
    _add_timestamps("plot_threads")
    _add_timestamps("world_rules")

    voice_columns = _columns("style_voice_cards")
    if "addressing_rules" not in voice_columns:
        op.add_column(
            "style_voice_cards",
            sa.Column(
                "addressing_rules",
                JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )
    _add_timestamps("style_voice_cards")

    _rename_or_add(
        "style_tone_anchors",
        old="description_intensity",
        new="description_density",
        type_=sa.String(200),
    )
    _rename_or_add(
        "style_tone_anchors",
        old="adult_content_policy",
        new="adult_violence_expression",
        type_=sa.Text(),
    )
    _rename_or_add(
        "style_tone_anchors",
        old="forbidden_expressions",
        new="forbidden_modern_expressions",
        type_=JSONB(),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )
    tone_columns = _columns("style_tone_anchors")
    if "anchor_json" not in tone_columns:
        op.add_column(
            "style_tone_anchors",
            sa.Column("anchor_json", JSONB(), nullable=True),
        )
    op.alter_column(
        "style_tone_anchors",
        "psychology_ratio",
        existing_type=sa.Float(),
        type_=sa.String(200),
        postgresql_using="psychology_ratio::text",
        existing_nullable=True,
    )
    op.alter_column(
        "style_tone_anchors",
        "dialogue_narration_ratio",
        existing_type=sa.Float(),
        type_=sa.String(200),
        postgresql_using="dialogue_narration_ratio::text",
        existing_nullable=True,
    )
    _add_timestamps("style_tone_anchors")


def downgrade() -> None:
    tone_columns = _columns("style_tone_anchors")
    _drop_columns_if_present(
        "style_tone_anchors", "created_at", "updated_at", "anchor_json"
    )
    if "psychology_ratio" in tone_columns:
        op.alter_column(
            "style_tone_anchors",
            "psychology_ratio",
            existing_type=sa.String(200),
            type_=sa.Float(),
            postgresql_using=(
                "CASE WHEN psychology_ratio ~ '^[+-]?[0-9]+([.][0-9]+)?$' "
                "THEN psychology_ratio::double precision ELSE NULL END"
            ),
            existing_nullable=True,
        )
    if "dialogue_narration_ratio" in tone_columns:
        op.alter_column(
            "style_tone_anchors",
            "dialogue_narration_ratio",
            existing_type=sa.String(200),
            type_=sa.Float(),
            postgresql_using=(
                "CASE WHEN dialogue_narration_ratio ~ '^[+-]?[0-9]+([.][0-9]+)?$' "
                "THEN dialogue_narration_ratio::double precision ELSE NULL END"
            ),
            existing_nullable=True,
        )
    for new, old in (
        ("description_density", "description_intensity"),
        ("adult_violence_expression", "adult_content_policy"),
        ("forbidden_modern_expressions", "forbidden_expressions"),
    ):
        current = _columns("style_tone_anchors")
        if new in current and old not in current:
            op.alter_column("style_tone_anchors", new, new_column_name=old)

    _drop_columns_if_present(
        "style_voice_cards", "created_at", "updated_at", "addressing_rules"
    )
    _drop_columns_if_present("world_rules", "created_at", "updated_at")
    _drop_columns_if_present(
        "plot_threads", "created_at", "updated_at", "version"
    )

    op.execute("DROP INDEX IF EXISTS ix_technique_cards_book_status")
    _drop_columns_if_present(
        "technique_cards", "created_at", "updated_at", "book_id"
    )
    _drop_columns_if_present(
        "timeline_events", "created_at", "updated_at", "description"
    )
    _drop_columns_if_present(
        "rewrite_patches",
        "created_at",
        "updated_at",
        "source_run_id",
        "resolved_issue_ids",
        "preserved_before",
        "preserved_after",
    )
    _drop_columns_if_present(
        "review_issues", "created_at", "updated_at", "resolved"
    )
    for table in (
        "retrieval_candidates",
        "retrieval_judgements",
        "retrieval_runs",
    ):
        _drop_columns_if_present(table, "created_at", "updated_at")
    _drop_columns_if_present(
        "relationship_events",
        "created_at",
        "updated_at",
        "old_state",
        "new_state",
    )
    _drop_columns_if_present("query_plans", "created_at", "updated_at")
    _drop_columns_if_present(
        "llm_usage_events", "created_at", "updated_at", "cost_usd"
    )
    _drop_columns_if_present(
        "item_events", "created_at", "updated_at", "character_id", "description"
    )
    _drop_columns_if_present(
        "human_interventions", "created_at", "updated_at", "resolved"
    )
    _drop_columns_if_present(
        "genre_profiles",
        "source_kind",
        "source_ref_json",
        "source_url",
        "research_task_id",
        "research_document_id",
        "imported_at",
    )
    for table in (
        "editorial_review_policies",
        "editorial_rubric_templates",
        "entity_aliases",
    ):
        _drop_columns_if_present(table, "created_at", "updated_at")
    _drop_columns_if_present(
        "character_state_snapshots",
        "created_at",
        "updated_at",
        "source_run_id",
        "is_locked",
    )
    _drop_columns_if_present(
        "character_state_events", "created_at", "updated_at", "source_run_id"
    )
    _drop_columns_if_present(
        "chapter_versions",
        "proposal_id",
        "experiment_id",
        "canary_status",
        "rolled_back_from_id",
    )
    _drop_columns_if_present(
        "agent_run_outputs", "experience_refs", "technique_refs"
    )
