"""v8.1: make per-attempt usage and retrieval candidates durable."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


revision = "0010_attempt_usage_retrieval"
down_revision = "0009_prompt_runtime_contract"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:table "
                "AND column_name=:column"
            ),
            {"table": table, "column": column},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_column(conn, "llm_usage_events", "attempt_no"):
        op.add_column(
            "llm_usage_events",
            sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        )

    retrieval_columns = (
        ("source_id", UUID(as_uuid=True), True),
        ("selected", sa.Boolean(), False),
        ("candidate_json", JSONB(), True),
    )
    for name, column_type, nullable in retrieval_columns:
        if not _has_column(conn, "retrieval_candidates", name):
            op.add_column(
                "retrieval_candidates",
                sa.Column(
                    name,
                    column_type,
                    nullable=nullable,
                    server_default="false" if name == "selected" else None,
                ),
            )

    if not _has_column(conn, "retrieval_judgements", "rank"):
        op.add_column(
            "retrieval_judgements",
            sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    for table, column in (
        ("retrieval_judgements", "rank"),
        ("retrieval_candidates", "candidate_json"),
        ("retrieval_candidates", "selected"),
        ("retrieval_candidates", "source_id"),
        ("llm_usage_events", "attempt_no"),
    ):
        if _has_column(conn, table, column):
            op.drop_column(table, column)