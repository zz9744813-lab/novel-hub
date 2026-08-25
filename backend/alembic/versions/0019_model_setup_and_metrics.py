"""v9.6: model setup & metrics (detection runs + performance probe fields).

model_autoconfig_runs; model_health_probes per-run performance columns;
agent_model_bindings auto-assignment snapshot columns. Spec §30, §48, §61.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0019_model_setup_and_metrics"
down_revision = "0018_model_autopilot"
branch_labels = None
depends_on = None


def _has_table(conn, table: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name=:t"
            ),
            {"t": table},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "model_autoconfig_runs"):
        op.create_table(
            "model_autoconfig_runs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("action", sa.String(30), nullable=False),  # detect|detect_and_configure
            sa.Column("scan_mode", sa.String(12), nullable=False, server_default="quick"),
            sa.Column("status", sa.String(20), nullable=False, server_default="queued", index=True),
            sa.Column("phase", sa.String(30), nullable=False, server_default="queued"),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("current_model", sa.String(300), nullable=True),
            sa.Column("finished", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("idempotency_key", sa.String(160), nullable=True),
            sa.Column("catalog_hash", sa.String(64), nullable=True),
            sa.Column("detected_models", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("healthy_models", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("eligible_models", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("recommendation_json", JSONB(), nullable=True),
            sa.Column("before_snapshot", JSONB(), nullable=True),
            sa.Column("after_snapshot", JSONB(), nullable=True),
            sa.Column("error_json", JSONB(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )

    # v9.6 §48: performance probe rows carry real token/throughput stats
    pcols = [c["name"] for c in sa.inspect(conn).get_columns("model_health_probes")]
    if "prompt_tokens" not in pcols:
        op.add_column("model_health_probes", sa.Column("prompt_tokens", sa.Integer(), nullable=True))
    if "completion_tokens" not in pcols:
        op.add_column("model_health_probes", sa.Column("completion_tokens", sa.Integer(), nullable=True))
    if "tokens_per_second" not in pcols:
        op.add_column("model_health_probes", sa.Column("tokens_per_second", sa.Float(), nullable=True))

    # v9.6 §61: bindings record the auto-assignment snapshot for rollback/verify
    bcols = [c["name"] for c in sa.inspect(conn).get_columns("agent_model_bindings")]
    if "auto_assignment_snapshot" not in bcols:
        op.add_column("agent_model_bindings", sa.Column("auto_assignment_snapshot", JSONB(), nullable=True, server_default="{}"))
    if "last_auto_config_run_id" not in bcols:
        op.add_column("agent_model_bindings", sa.Column("last_auto_config_run_id", UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_model_bindings", "last_auto_config_run_id")
    op.drop_column("agent_model_bindings", "auto_assignment_snapshot")
    op.drop_column("model_health_probes", "tokens_per_second")
    op.drop_column("model_health_probes", "completion_tokens")
    op.drop_column("model_health_probes", "prompt_tokens")
    op.drop_table("model_autoconfig_runs")
