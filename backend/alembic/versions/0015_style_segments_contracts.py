"""v9.2: style sample segments + scene style contracts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0015_style_segments_contracts"
down_revision = "0014_style_intelligence"
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


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "style_sample_segments"):
        op.create_table(
            "style_sample_segments",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("reference_sample_id", UUID(as_uuid=True), sa.ForeignKey("reference_samples.id"), nullable=False),
            sa.Column("segment_no", sa.Integer(), nullable=False),
            sa.Column("scene_type", sa.String(32), nullable=True),
            sa.Column("source_position_bucket", sa.String(16), nullable=True),
            sa.Column("start_char", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("end_char", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("content_hash", sa.String(64), nullable=True),
            sa.Column("metric_vector", JSONB(), nullable=False, server_default="{}"),
            sa.Column("semantic_analysis", JSONB(), nullable=False, server_default="{}"),
            *_ts(),
        )
        op.create_index("ix_style_sample_segments_ref", "style_sample_segments", ["reference_sample_id"])

    if not _has_table(conn, "scene_style_contracts"):
        op.create_table(
            "scene_style_contracts",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
            sa.Column("style_profile_id", UUID(as_uuid=True), sa.ForeignKey("style_profiles.id"), nullable=True),
            sa.Column("scene_no", sa.Integer(), nullable=False),
            sa.Column("scene_mode", sa.String(32), nullable=False, server_default="general"),
            sa.Column("targets", JSONB(), nullable=False, server_default="{}"),
            sa.Column("semantic", JSONB(), nullable=False, server_default="{}"),
            sa.Column("avoid", JSONB(), nullable=False, server_default="[]"),
            sa.UniqueConstraint("book_id", "scene_no", name="uq_scene_style_contracts_book_scene"),
            *_ts(),
        )
        op.create_index("ix_scene_style_contracts_book", "scene_style_contracts", ["book_id"])


def downgrade() -> None:
    op.drop_table("scene_style_contracts")
    op.drop_table("style_sample_segments")
