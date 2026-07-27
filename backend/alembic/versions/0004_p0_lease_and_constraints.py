"""P0-04 chapter_tasks lease fields + production constraints."""
from alembic import op
import sqlalchemy as sa

revision = "0004_p0_lease_and_constraints"
down_revision = "0003_v74_incremental"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: columns may already exist from earlier manual/partial upgrades
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='chapter_tasks' AND column_name='lease_owner'
          ) THEN
            ALTER TABLE chapter_tasks ADD COLUMN lease_owner VARCHAR(200);
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='chapter_tasks' AND column_name='lease_expires_at'
          ) THEN
            ALTER TABLE chapter_tasks ADD COLUMN lease_expires_at TIMESTAMPTZ;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='chapter_tasks' AND column_name='heartbeat_at'
          ) THEN
            ALTER TABLE chapter_tasks ADD COLUMN heartbeat_at TIMESTAMPTZ;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='chapter_tasks' AND column_name='attempt_no'
          ) THEN
            ALTER TABLE chapter_tasks ADD COLUMN attempt_no INTEGER NOT NULL DEFAULT 0;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='chapter_tasks' AND column_name='last_error_code'
          ) THEN
            ALTER TABLE chapter_tasks ADD COLUMN last_error_code VARCHAR(100);
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name='chapter_tasks' AND column_name='last_error_detail'
          ) THEN
            ALTER TABLE chapter_tasks ADD COLUMN last_error_detail TEXT;
          END IF;
        END $$;
        """
    )
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
    # Best-effort; leave columns if already used in production
    pass
