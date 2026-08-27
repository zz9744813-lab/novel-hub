"""v9.8 content-addressed ability and context evidence.

Revision ID: 0021_v98_model_evidence
Revises: 0020_v97_system_closure
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0021_v98_model_evidence"
down_revision = "0020_v97_system_closure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("model_catalog") as batch:
        batch.add_column(sa.Column("endpoint_identity_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("upstream_identity_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("ability_evaluation_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("ability_identity_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("ability_suite_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("ability_evaluator_revision", sa.String(40), nullable=True))
        batch.add_column(sa.Column("ability_reuse_reason", sa.String(40), nullable=True))
        batch.add_column(
            sa.Column(
                "ability_source_run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("model_eval_runs.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("ability_completed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("context_evaluation_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("context_identity_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("context_suite_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("context_evaluator_revision", sa.String(40), nullable=True))
        batch.add_column(
            sa.Column(
                "context_source_run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("model_eval_runs.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("context_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_model_catalog_ability_evaluation_key",
        "model_catalog",
        ["ability_evaluation_key"],
    )
    op.create_index(
        "ix_model_catalog_context_evaluation_key",
        "model_catalog",
        ["context_evaluation_key"],
    )

    with op.batch_alter_table("model_eval_runs") as batch:
        batch.add_column(sa.Column("ability_evaluation_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("ability_identity_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("ability_suite_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("ability_evaluator_revision", sa.String(40), nullable=True))
        batch.add_column(
            sa.Column(
                "ability_source_run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("model_eval_runs.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("context_evaluation_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("context_identity_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("context_suite_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("context_evaluator_revision", sa.String(40), nullable=True))
        batch.add_column(
            sa.Column(
                "context_source_run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("model_eval_runs.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("reuse_reason", sa.String(40), nullable=True))
        batch.add_column(sa.Column("triggered_by", sa.String(40), nullable=True))
        batch.add_column(
            sa.Column("force_requested", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("gateway_calls", sa.Integer(), nullable=True))
    op.create_index(
        "ix_model_eval_runs_ability_evaluation_key",
        "model_eval_runs",
        ["ability_evaluation_key"],
    )
    op.create_index(
        "ix_model_eval_runs_ability_source_run_id",
        "model_eval_runs",
        ["ability_source_run_id"],
    )
    op.create_index(
        "ix_model_eval_runs_context_evaluation_key",
        "model_eval_runs",
        ["context_evaluation_key"],
    )
    op.create_index(
        "ix_model_eval_runs_context_source_run_id",
        "model_eval_runs",
        ["context_source_run_id"],
    )
    # Older builds had no database-level active claim. Keep the newest active
    # row per model/mode and close any duplicates before adding the partial
    # unique index, so upgrade remains safe on a live database.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY model_catalog_id, mode
                       ORDER BY COALESCE(started_at, created_at) DESC, id DESC
                   ) AS claim_rank
            FROM model_eval_runs
            WHERE status IN ('running', 'in_progress')
        )
        UPDATE model_eval_runs AS runs
        SET status = 'failed',
            finished_at = COALESCE(runs.finished_at, now()),
            result_summary = COALESCE(runs.result_summary, '{}'::jsonb)
                || jsonb_build_object(
                    'execution_complete', false,
                    'error', 'superseded_during_v98_migration'
                )
        FROM ranked
        WHERE runs.id = ranked.id AND ranked.claim_rank > 1
        """
    )
    op.create_index(
        "uq_model_eval_runs_active_claim",
        "model_eval_runs",
        ["model_catalog_id", "mode"],
        unique=True,
        postgresql_where=sa.text("status IN ('running', 'in_progress')"),
    )

    with op.batch_alter_table("model_context_profiles") as batch:
        batch.add_column(sa.Column("context_evaluation_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("context_identity_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("context_suite_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("context_evaluator_revision", sa.String(40), nullable=True))
        batch.add_column(
            sa.Column(
                "context_source_run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("model_eval_runs.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
    op.create_index(
        "ix_model_context_profiles_context_evaluation_key",
        "model_context_profiles",
        ["context_evaluation_key"],
    )

    with op.batch_alter_table("model_role_scores") as batch:
        batch.add_column(sa.Column("benchmark_evidence_key", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column(
                "benchmark_source_run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("model_eval_runs.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("benchmark_passed", sa.Boolean(), nullable=True))
    op.create_index(
        "ix_model_role_scores_benchmark_source_run_id",
        "model_role_scores",
        ["benchmark_source_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_role_scores_benchmark_source_run_id",
        table_name="model_role_scores",
    )
    with op.batch_alter_table("model_role_scores") as batch:
        batch.drop_column("benchmark_passed")
        batch.drop_column("benchmark_source_run_id")
        batch.drop_column("benchmark_evidence_key")

    op.drop_index(
        "ix_model_context_profiles_context_evaluation_key",
        table_name="model_context_profiles",
    )
    with op.batch_alter_table("model_context_profiles") as batch:
        batch.drop_column("context_source_run_id")
        batch.drop_column("context_evaluator_revision")
        batch.drop_column("context_suite_hash")
        batch.drop_column("context_identity_hash")
        batch.drop_column("context_evaluation_key")

    op.drop_index("uq_model_eval_runs_active_claim", table_name="model_eval_runs")
    op.drop_index(
        "ix_model_eval_runs_context_source_run_id",
        table_name="model_eval_runs",
    )
    op.drop_index(
        "ix_model_eval_runs_context_evaluation_key",
        table_name="model_eval_runs",
    )
    op.drop_index(
        "ix_model_eval_runs_ability_source_run_id",
        table_name="model_eval_runs",
    )
    op.drop_index(
        "ix_model_eval_runs_ability_evaluation_key",
        table_name="model_eval_runs",
    )
    with op.batch_alter_table("model_eval_runs") as batch:
        batch.drop_column("gateway_calls")
        batch.drop_column("force_requested")
        batch.drop_column("triggered_by")
        batch.drop_column("reuse_reason")
        batch.drop_column("context_source_run_id")
        batch.drop_column("context_evaluator_revision")
        batch.drop_column("context_suite_hash")
        batch.drop_column("context_identity_hash")
        batch.drop_column("context_evaluation_key")
        batch.drop_column("ability_source_run_id")
        batch.drop_column("ability_evaluator_revision")
        batch.drop_column("ability_suite_hash")
        batch.drop_column("ability_identity_hash")
        batch.drop_column("ability_evaluation_key")

    op.drop_index(
        "ix_model_catalog_context_evaluation_key",
        table_name="model_catalog",
    )
    op.drop_index(
        "ix_model_catalog_ability_evaluation_key",
        table_name="model_catalog",
    )
    with op.batch_alter_table("model_catalog") as batch:
        batch.drop_column("context_completed_at")
        batch.drop_column("context_source_run_id")
        batch.drop_column("context_evaluator_revision")
        batch.drop_column("context_suite_hash")
        batch.drop_column("context_identity_hash")
        batch.drop_column("context_evaluation_key")
        batch.drop_column("ability_completed_at")
        batch.drop_column("ability_source_run_id")
        batch.drop_column("ability_reuse_reason")
        batch.drop_column("ability_evaluator_revision")
        batch.drop_column("ability_suite_hash")
        batch.drop_column("ability_identity_hash")
        batch.drop_column("ability_evaluation_key")
        batch.drop_column("upstream_identity_hash")
        batch.drop_column("endpoint_identity_hash")
