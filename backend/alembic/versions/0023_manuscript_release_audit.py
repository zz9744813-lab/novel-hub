"""Persist deterministic and blind manuscript release evidence.

Revision ID: 0023_manuscript_release
Revises: 0022_session_created_unique
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0023_manuscript_release"
down_revision = "0022_session_created_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manuscript_release_audits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "book_id",
            UUID(as_uuid=True),
            sa.ForeignKey("books.id"),
            nullable=False,
        ),
        sa.Column("production_pack_id", sa.String(120), nullable=False),
        sa.Column("production_pack_revision", sa.Integer(), nullable=False),
        sa.Column("production_pack_sha256", sa.String(64), nullable=False),
        sa.Column("gate_version", sa.String(64), nullable=False),
        sa.Column("manuscript_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "sample_chapter_nos",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "deterministic_report",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("blind_report", JSONB(), nullable=True),
        sa.Column(
            "blind_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "book_id",
            "manuscript_hash",
            "gate_version",
            "production_pack_sha256",
            name="uq_manuscript_release_evidence",
        ),
    )
    op.create_index(
        "ix_manuscript_release_audits_book_id",
        "manuscript_release_audits",
        ["book_id"],
    )
    op.create_index(
        "ix_manuscript_release_audits_status",
        "manuscript_release_audits",
        ["status"],
    )
    op.create_index(
        "ix_manuscript_release_pack",
        "manuscript_release_audits",
        ["production_pack_id", "production_pack_revision"],
    )


def downgrade() -> None:
    op.drop_index("ix_manuscript_release_pack", table_name="manuscript_release_audits")
    op.drop_index(
        "ix_manuscript_release_audits_status",
        table_name="manuscript_release_audits",
    )
    op.drop_index(
        "ix_manuscript_release_audits_book_id",
        table_name="manuscript_release_audits",
    )
    op.drop_table("manuscript_release_audits")
