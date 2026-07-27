"""PR-01: chapter_runs, step_runs, outbox, active_run_id, version metadata."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0006_pipeline_reliability"
down_revision = "0005_chapter_state_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    def has_col(table: str, col: str) -> bool:
        return bool(
            conn.execute(
                sa.text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name=:t AND column_name=:c"
                ),
                {"t": table, "c": col},
            ).scalar()
        )

    def has_table(table: str) -> bool:
        return bool(
            conn.execute(
                sa.text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name=:t"
                ),
                {"t": table},
            ).scalar()
        )

    if not has_col("chapters", "active_run_id"):
        op.add_column(
            "chapters",
            sa.Column("active_run_id", UUID(as_uuid=True), nullable=True),
        )

    if not has_table("chapter_runs"):
        op.create_table(
            "chapter_runs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
            sa.Column(
                "chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id"), nullable=False
            ),
            sa.Column("chapter_no", sa.Integer(), nullable=False),
            sa.Column(
                "outline_version_id",
                UUID(as_uuid=True),
                sa.ForeignKey("outline_versions.id"),
                nullable=False,
            ),
            sa.Column("pipeline_version", sa.Text(), nullable=False, server_default="pipeline-v2"),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("current_step", sa.Text(), nullable=True),
            sa.Column(
                "control_requested", sa.Text(), nullable=False, server_default="none"
            ),
            sa.Column("request_id", sa.Text(), nullable=False),
            sa.Column(
                "resume_from_run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("chapter_runs.id"),
                nullable=True,
            ),
            sa.Column("model_binding_snapshot", JSONB(), nullable=False, server_default="{}"),
            sa.Column("budget_snapshot", JSONB(), nullable=False, server_default="{}"),
            sa.Column("lease_owner", sa.Text(), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_code", sa.Text(), nullable=True),
            sa.Column("error_detail", JSONB(), nullable=True),
            sa.Column("created_by", sa.Text(), nullable=False, server_default="api"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.UniqueConstraint("chapter_id", "request_id", name="uq_chapter_runs_chapter_request"),
        )
        op.create_index("ix_chapter_runs_book_id", "chapter_runs", ["book_id"])
        op.create_index("ix_chapter_runs_chapter_id", "chapter_runs", ["chapter_id"])
        op.create_index("ix_chapter_runs_status", "chapter_runs", ["status"])
        op.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_chapter_runs_one_active
            ON chapter_runs(chapter_id)
            WHERE status IN ('queued', 'running', 'paused', 'waiting_dependency', 'retryable')
            """
        )

    # FK active_run_id after chapter_runs exists
    if has_col("chapters", "active_run_id"):
        try:
            op.create_foreign_key(
                "fk_chapters_active_run",
                "chapters",
                "chapter_runs",
                ["active_run_id"],
                ["id"],
            )
        except Exception:
            pass

    if not has_table("chapter_step_runs"):
        op.create_table(
            "chapter_step_runs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "chapter_run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("chapter_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("step_name", sa.Text(), nullable=False),
            sa.Column("step_key", sa.Text(), nullable=False),
            sa.Column("attempt_no", sa.Integer(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("input_hash", sa.String(64), nullable=False),
            sa.Column("output_hash", sa.String(64), nullable=True),
            sa.Column("output_json", JSONB(), nullable=True),
            sa.Column("output_text", sa.Text(), nullable=True),
            sa.Column("artifact_ref", JSONB(), nullable=True),
            sa.Column(
                "reused_from_step_id",
                UUID(as_uuid=True),
                sa.ForeignKey("chapter_step_runs.id"),
                nullable=True,
            ),
            sa.Column("error_code", sa.Text(), nullable=True),
            sa.Column("error_detail", JSONB(), nullable=True),
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "chapter_run_id",
                "step_key",
                "attempt_no",
                name="uq_chapter_step_runs_attempt",
            ),
        )
        op.create_index("ix_chapter_step_runs_run", "chapter_step_runs", ["chapter_run_id"])
        op.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_step_success_by_input
            ON chapter_step_runs(chapter_run_id, step_key, input_hash)
            WHERE status IN ('succeeded', 'reused')
            """
        )

    if not has_table("chapter_dispatch_outbox"):
        op.create_table(
            "chapter_dispatch_outbox",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "chapter_run_id",
                UUID(as_uuid=True),
                sa.ForeignKey("chapter_runs.id"),
                nullable=False,
            ),
            sa.Column("dedupe_key", sa.Text(), nullable=False),
            sa.Column(
                "event_type", sa.Text(), nullable=False, server_default="dispatch_chapter_run"
            ),
            sa.Column("payload", JSONB(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "available_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_by", sa.Text(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.UniqueConstraint("dedupe_key", name="uq_outbox_dedupe_key"),
        )
        op.create_index(
            "ix_outbox_pending",
            "chapter_dispatch_outbox",
            ["status", "available_at"],
        )

    if not has_col("chapter_versions", "version_kind"):
        op.add_column("chapter_versions", sa.Column("version_kind", sa.Text(), nullable=True))
    if not has_col("chapter_versions", "content_hash"):
        op.add_column(
            "chapter_versions", sa.Column("content_hash", sa.String(64), nullable=True)
        )
    if not has_col("chapter_versions", "chapter_run_id"):
        op.add_column(
            "chapter_versions",
            sa.Column("chapter_run_id", UUID(as_uuid=True), nullable=True),
        )
    if not has_col("chapter_versions", "finalization_key"):
        op.add_column(
            "chapter_versions",
            sa.Column("finalization_key", sa.String(64), nullable=True),
        )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_finalization_key
        ON chapter_versions(chapter_id, finalization_key)
        WHERE finalization_key IS NOT NULL
        """
    )

    if not has_col("agent_runs", "chapter_run_id"):
        op.add_column(
            "agent_runs",
            sa.Column("chapter_run_id", UUID(as_uuid=True), nullable=True),
        )
    if not has_col("agent_runs", "step_run_id"):
        op.add_column(
            "agent_runs",
            sa.Column("step_run_id", UUID(as_uuid=True), nullable=True),
        )
    if not has_col("agent_runs", "error_code"):
        op.add_column("agent_runs", sa.Column("error_code", sa.Text(), nullable=True))

    if not has_col("chapter_state_events", "chapter_run_id"):
        op.add_column(
            "chapter_state_events",
            sa.Column("chapter_run_id", UUID(as_uuid=True), nullable=True),
        )
    if not has_col("chapter_state_events", "step_key"):
        op.add_column(
            "chapter_state_events",
            sa.Column("step_key", sa.Text(), nullable=True),
        )
    if not has_col("chapter_state_events", "reason_code"):
        op.add_column(
            "chapter_state_events",
            sa.Column("reason_code", sa.Text(), nullable=True),
        )
    if not has_col("chapter_state_events", "detail"):
        op.add_column(
            "chapter_state_events",
            sa.Column("detail", JSONB(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    # Non-destructive downgrade intentionally minimal
    pass
