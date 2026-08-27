"""Include newly-created sessions in the per-book active-session invariant.

Revision ID: 0022_session_created_unique
Revises: 0021_v98_model_evidence
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_session_created_unique"
down_revision = "0021_v98_model_evidence"
branch_labels = None
depends_on = None


_INDEX_NAME = "uq_active_writing_session_per_book"


def upgrade() -> None:
    # The API has always treated ``created`` as book-owning.  Older schemas
    # omitted it from the partial unique index.  Preserve the existing
    # non-created owner and retire only duplicate preflight rows before the
    # stronger index is installed.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    status,
                    row_number() OVER (
                        PARTITION BY book_id
                        ORDER BY
                            CASE WHEN status = 'created' THEN 1 ELSE 0 END,
                            updated_at DESC,
                            created_at DESC,
                            id DESC
                    ) AS owner_rank
                FROM writing_sessions
                WHERE status IN (
                    'created', 'running', 'pausing', 'paused',
                    'waiting_editorial', 'blocked'
                )
            )
            UPDATE writing_sessions AS ws
            SET
                status = 'failed',
                stop_reason = 'migration_duplicate_active',
                stop_detail = jsonb_build_object(
                    'migration', '0022_session_created_unique'
                ),
                completed_at = COALESCE(ws.completed_at, now()),
                updated_at = now()
            FROM ranked
            WHERE ws.id = ranked.id
              AND ranked.owner_rank > 1
              AND ranked.status = 'created'
            """
        )
    )
    op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
    op.execute(
        f"""
        CREATE UNIQUE INDEX {_INDEX_NAME}
        ON writing_sessions(book_id)
        WHERE status IN (
            'created', 'running', 'pausing', 'paused',
            'waiting_editorial', 'blocked'
        )
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
    op.execute(
        f"""
        CREATE UNIQUE INDEX {_INDEX_NAME}
        ON writing_sessions(book_id)
        WHERE status IN (
            'running', 'pausing', 'paused',
            'waiting_editorial', 'blocked'
        )
        """
    )
