"""v9.2: style intelligence engine tables (style_profiles + chapter_style_scores)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0014_style_intelligence"
down_revision = "0013_research_source_certification"
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

    if not _has_table(conn, "style_profiles"):
        op.create_table(
            "style_profiles",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("metric_vector", JSONB(), nullable=False, server_default="{}"),
            sa.Column("metric_ranges", JSONB(), nullable=False, server_default="{}"),
            sa.Column("fingerprint", JSONB(), nullable=False, server_default="[]"),
            sa.Column("narrative_profile", JSONB(), nullable=False, server_default="{}"),
            sa.Column("dialogue_profile", JSONB(), nullable=False, server_default="{}"),
            sa.Column("rhythm_profile", JSONB(), nullable=False, server_default="{}"),
            sa.Column("emotion_expression_profile", JSONB(), nullable=False, server_default="{}"),
            sa.Column("technique_profile", JSONB(), nullable=False, server_default="{}"),
            sa.Column("scene_mode_profiles", JSONB(), nullable=False, server_default="{}"),
            sa.Column("confidence_by_dimension", JSONB(), nullable=False, server_default="{}"),
            sa.Column("analyzer_version", sa.String(64), nullable=True),
            sa.Column("metric_engine_version", sa.String(64), nullable=True),
            sa.Column("approved_by", sa.String(200), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            *_ts(),
        )
        op.create_index("ix_style_profiles_book_id", "style_profiles", ["book_id"])

    if not _has_table(conn, "chapter_style_scores"):
        op.create_table(
            "chapter_style_scores",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
            sa.Column("chapter_no", sa.Integer(), nullable=False),
            sa.Column("surface_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("rhythm_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("dialogue_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("narrative_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("emotion_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("voice_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("overall_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("distance_to_profile", sa.Float(), nullable=False, server_default="0"),
            sa.Column("metric_json", JSONB(), nullable=False, server_default="{}"),
            sa.UniqueConstraint("book_id", "chapter_no", name="uq_chapter_style_scores_book_chapter"),
            *_ts(),
        )
        op.create_index("ix_chapter_style_scores_book_id", "chapter_style_scores", ["book_id"])


def downgrade() -> None:
    op.drop_table("chapter_style_scores")
    op.drop_table("style_profiles")
