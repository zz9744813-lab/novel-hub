"""v9.4: time-window autonomous writing session tables.

writing_sessions / writing_session_events / session_advance_outbox
plus chapter_runs.writing_session_id. See v9.4 spec §4.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0017_writing_sessions"
down_revision = "0016_merge_v93_heads"
branch_labels = None
depends_on = None


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    conn = op.get_bind()

    if not conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name='writing_sessions'")
    ).scalar():
        op.create_table(
            "writing_sessions",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False, index=True),
            sa.Column("mode", sa.String(20), nullable=False, server_default="duration"),
            sa.Column("requested_duration_minutes", sa.Integer(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(30), nullable=False, server_default="created", index=True),
            sa.Column("control_requested", sa.String(15), nullable=False, server_default="none"),
            sa.Column("current_chapter_id", UUID(as_uuid=True), nullable=True),
            sa.Column("current_chapter_no", sa.Integer(), nullable=True),
            sa.Column("current_chapter_run_id", UUID(as_uuid=True), nullable=True),
            sa.Column("chapters_started", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("chapters_completed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("words_generated", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("create_idempotency_key", sa.String(128), nullable=True, index=True),
            sa.Column("policy_version", sa.String(64), nullable=True),
            sa.Column("policy_snapshot", JSONB(), nullable=False, server_default="{}"),
            sa.Column("stop_reason", sa.String(60), nullable=True),
            sa.Column("stop_detail", JSONB(), nullable=True),
            sa.Column("reconcile_lease_owner", sa.String(200), nullable=True),
            sa.Column("reconcile_lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            *_ts(),
        )
        op.create_index(
            "ix_writing_sessions_book_key",
            "writing_sessions",
            ["book_id", "create_idempotency_key"],
        )
        # One active session per book (spec §7)
        op.execute(
            """
            CREATE UNIQUE INDEX uq_active_writing_session_per_book
            ON writing_sessions(book_id)
            WHERE status IN (
                'running', 'pausing', 'paused',
                'waiting_editorial', 'blocked'
            )
            """
        )

    if not conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name='writing_session_events'")
    ).scalar():
        op.create_table(
            "writing_session_events",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("writing_sessions.id"), nullable=False),
            sa.Column("event_type", sa.String(50), nullable=False),
            sa.Column("source_type", sa.String(50), nullable=True),
            sa.Column("source_id", sa.String(64), nullable=True),
            sa.Column("dedupe_key", sa.String(200), nullable=True),
            sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        )
        op.create_index("ix_wse_session_id", "writing_session_events", ["session_id"])
        op.create_unique_constraint(
            "uq_wse_session_dedupe",
            "writing_session_events",
            ["session_id", "dedupe_key"],
        )

    if not conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name='session_advance_outbox'")
    ).scalar():
        op.create_table(
            "session_advance_outbox",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("writing_session_id", UUID(as_uuid=True), sa.ForeignKey("writing_sessions.id"), nullable=False),
            sa.Column("completed_run_id", UUID(as_uuid=True), nullable=True),
            sa.Column("event_type", sa.String(50), nullable=False, server_default="advance_writing_session"),
            sa.Column("dedupe_key", sa.String(200), nullable=False, unique=True),
            sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(30), nullable=False, server_default="pending", index=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_by", sa.String(200), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
            *_ts(),
        )
        op.create_index("ix_sao_session_status", "session_advance_outbox", ["writing_session_id", "status"])

    # chapter_runs.writing_session_id (spec §4.4)
    op.add_column("chapter_runs", sa.Column("writing_session_id", UUID(as_uuid=True), nullable=True))
    op.create_index(
        "ix_chapter_runs_session_status",
        "chapter_runs",
        ["writing_session_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_chapter_runs_session_status", table_name="chapter_runs")
    op.drop_column("chapter_runs", "writing_session_id")
    op.drop_table("session_advance_outbox")
    op.drop_table("writing_session_events")
    op.execute("DROP INDEX IF EXISTS uq_active_writing_session_per_book")
    op.drop_index("ix_writing_sessions_book_key", table_name="writing_sessions")
    op.drop_table("writing_sessions")
