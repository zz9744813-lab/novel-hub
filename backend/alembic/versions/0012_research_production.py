"""v9.1: research production tables (sources, tasks, documents, exports)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0012_research_production"
down_revision = "0011_v9_cognitive_causal"
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


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "research_sources"):
        op.create_table(
            "research_sources",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("code", sa.String(64), nullable=False, unique=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("base_url", sa.Text(), nullable=False),
            sa.Column("chapter_list_selector", sa.Text(), nullable=True),
            sa.Column("title_selector", sa.Text(), nullable=True),
            sa.Column("content_selector", sa.Text(), nullable=False),
            sa.Column("pagination_selector", sa.Text(), nullable=True),
            sa.Column("encoding", sa.String(32), nullable=False, server_default="utf-8"),
            sa.Column("rate_limit", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column(
                "verification_status",
                sa.String(32),
                nullable=False,
                server_default="experimental",
            ),
            sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("config_json", JSONB(), nullable=False, server_default="{}"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )

    if not _has_table(conn, "research_tasks"):
        op.create_table(
            "research_tasks",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=True
            ),
            sa.Column(
                "source_id",
                UUID(as_uuid=True),
                sa.ForeignKey("research_sources.id"),
                nullable=False,
            ),
            sa.Column("target_url", sa.Text(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("discovered_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("current_url", sa.Text(), nullable=True),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error_detail", JSONB(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index("ix_research_tasks_book_id", "research_tasks", ["book_id"])
        op.create_index("ix_research_tasks_source_id", "research_tasks", ["source_id"])
        op.create_index("ix_research_tasks_status", "research_tasks", ["status"])

    if not _has_table(conn, "research_documents"):
        op.create_table(
            "research_documents",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "task_id",
                UUID(as_uuid=True),
                sa.ForeignKey("research_tasks.id"),
                nullable=False,
            ),
            sa.Column(
                "book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=True
            ),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("char_count", sa.Integer(), nullable=False),
            sa.Column("metadata_json", JSONB(), nullable=False, server_default="{}"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint("task_id", "source_url", name="uq_research_documents_task_url"),
        )
        op.create_index("ix_research_documents_task_id", "research_documents", ["task_id"])
        op.create_index("ix_research_documents_book_id", "research_documents", ["book_id"])

    if not _has_table(conn, "research_exports"):
        op.create_table(
            "research_exports",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "task_id",
                UUID(as_uuid=True),
                sa.ForeignKey("research_tasks.id"),
                nullable=False,
            ),
            sa.Column("format", sa.String(16), nullable=False),
            sa.Column("file_path", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("byte_size", sa.BigInteger(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
        )
        op.create_index("ix_research_exports_task_id", "research_exports", ["task_id"])


def downgrade() -> None:
    op.drop_table("research_exports")
    op.drop_table("research_documents")
    op.drop_table("research_tasks")
    op.drop_table("research_sources")
