"""P0-04 chapter_tasks lease fields + production constraints."""
from alembic import op
import sqlalchemy as sa

revision = "0004_p0_lease_and_constraints"
down_revision = "0003_v74_incremental"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chapter_tasks", sa.Column("lease_owner", sa.String(200), nullable=True))
    op.add_column("chapter_tasks", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chapter_tasks", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "chapter_tasks",
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("chapter_tasks", sa.Column("last_error_code", sa.String(100), nullable=True))
    op.add_column("chapter_tasks", sa.Column("last_error_detail", sa.Text(), nullable=True))

    # Unique constraints if not present (best-effort)
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'uq_chapter_versions_chapter_version'
          ) THEN
            ALTER TABLE chapter_versions
              ADD CONSTRAINT uq_chapter_versions_chapter_version UNIQUE (chapter_id, version);
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_column("chapter_tasks", "last_error_detail")
    op.drop_column("chapter_tasks", "last_error_code")
    op.drop_column("chapter_tasks", "attempt_no")
    op.drop_column("chapter_tasks", "heartbeat_at")
    op.drop_column("chapter_tasks", "lease_expires_at")
    op.drop_column("chapter_tasks", "lease_owner")
