"""v9.2: research source certification tables (probe runs + rule versions)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0013_research_source_certification"
down_revision = "0012_research_production"
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


def _ts_columns():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "research_source_probe_runs"):
        op.create_table(
            "research_source_probe_runs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("research_sources.id"), nullable=False),
            sa.Column("source_config_hash", sa.String(64), nullable=False),
            sa.Column("test_url", sa.Text(), nullable=False),
            sa.Column("probe_kind", sa.String(16), nullable=False, server_default="generic"),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("http_status", sa.Integer(), nullable=True),
            sa.Column("final_url", sa.Text(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("response_bytes", sa.Integer(), nullable=True),
            sa.Column("title_hit_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("list_link_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("content_hit_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("extracted_chars", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("anti_bot_type", sa.String(32), nullable=True),
            sa.Column("encoding_detected", sa.String(32), nullable=True),
            sa.Column("diagnostics_json", JSONB(), nullable=False, server_default="{}"),
            *_ts_columns(),
        )
        op.create_index("ix_probe_runs_source_id", "research_source_probe_runs", ["source_id"])
        op.create_index("ix_probe_runs_status", "research_source_probe_runs", ["status"])

    if not _has_table(conn, "research_source_versions"):
        op.create_table(
            "research_source_versions",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("research_sources.id"), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("config_hash", sa.String(64), nullable=False),
            sa.Column("config_json", JSONB(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(32), nullable=False, server_default="experimental"),
            sa.Column("created_by", sa.String(64), nullable=True),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("source_id", "version", name="uq_research_source_versions_source_version"),
            *_ts_columns(),
        )
        op.create_index("ix_source_versions_source_id", "research_source_versions", ["source_id"])


def downgrade() -> None:
    op.drop_table("research_source_versions")
    op.drop_table("research_source_probe_runs")
