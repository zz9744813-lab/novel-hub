"""v8.1: persist immutable Prompt Studio snapshots on every context package."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009_prompt_runtime_contract"
down_revision = "0008_import_entity_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='agent_context_packages' "
            "AND column_name='prompt_snapshot'"
        )
    ).scalar()
    if not exists:
        op.add_column(
            "agent_context_packages",
            sa.Column("prompt_snapshot", JSONB(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='agent_context_packages' "
            "AND column_name='prompt_snapshot'"
        )
    ).scalar()
    if exists:
        op.drop_column("agent_context_packages", "prompt_snapshot")
