"""v9.4: merge v9.3 divergent heads (editorial_learning_loop + style_segments_contracts).

Merge-only revision; no schema change. Collapses the two heads left by the
simultaneous v9.3 PRs into a single linear ancestor for 0017_writing_sessions.
"""
from alembic import op

revision = "0016_merge_v93_heads"
down_revision = (
    "0013_editorial_learning_loop",
    "0015_style_segments_contracts",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
