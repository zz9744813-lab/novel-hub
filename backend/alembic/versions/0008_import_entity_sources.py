"""v8.1: preserve import source references on extracted entities."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0008_import_entity_sources"
down_revision = "0007_v8_library_import_prompt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    for table in ("character_cards", "world_rules", "plot_threads"):
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:table AND column_name='source_refs'"
            ),
            {"table": table},
        ).scalar()
        if not exists:
            op.add_column(
                table,
                sa.Column("source_refs", JSONB(), nullable=False, server_default="[]"),
            )


def downgrade() -> None:
    # Source lineage is intentionally retained on downgrade.
    pass
