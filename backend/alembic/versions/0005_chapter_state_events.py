"""P1 CORE-001/002: chapter state_version + chapter_state_events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0005_chapter_state_events"
down_revision = "0004_p0_lease_and_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chapters",
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "chapters",
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "chapters",
        sa.Column("last_transition_reason", sa.Text(), nullable=True),
    )
    op.create_table(
        "chapter_state_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("from_state", sa.String(50), nullable=False),
        sa.Column("to_state", sa.String(50), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False, server_default="system"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("chapter_id", "state_version", name="uq_chapter_state_events_chapter_version"),
    )
    op.create_index("ix_chapter_state_events_chapter_id", "chapter_state_events", ["chapter_id"])
    op.create_index("ix_chapter_state_events_book_id", "chapter_state_events", ["book_id"])


def downgrade() -> None:
    op.drop_index("ix_chapter_state_events_book_id", table_name="chapter_state_events")
    op.drop_index("ix_chapter_state_events_chapter_id", table_name="chapter_state_events")
    op.drop_table("chapter_state_events")
    op.drop_column("chapters", "last_transition_reason")
    op.drop_column("chapters", "state_changed_at")
    op.drop_column("chapters", "state_version")
