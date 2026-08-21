"""v9.0: cognitive-causal narrative engine tables (core anchors, scene contracts, event edges)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0011_v9_cognitive_causal"
down_revision = "0010_attempt_usage_retrieval"
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

    if not _has_table(conn, "character_core_anchors"):
        op.create_table(
            "character_core_anchors",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
            sa.Column(
                "character_id",
                UUID(as_uuid=True),
                sa.ForeignKey("character_cards.id"),
                nullable=False,
            ),
            sa.Column("anchor_code", sa.String(32), nullable=False),
            sa.Column("anchor_type", sa.String(32), nullable=False),
            sa.Column("statement", sa.Text(), nullable=False),
            sa.Column("priority", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("rigidity", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("source_kind", sa.String(32), nullable=False, server_default="auto"),
            sa.Column("source_ref", JSONB(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("is_locked", sa.Boolean(), nullable=False, server_default="false"),
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
            sa.UniqueConstraint("character_id", "anchor_code"),
        )
        op.create_index(
            "idx_core_anchor_book_char", "character_core_anchors", ["book_id", "character_id"]
        )
        op.create_index(
            "ix_character_core_anchors_book_id", "character_core_anchors", ["book_id"]
        )
        op.create_index(
            "ix_character_core_anchors_character_id", "character_core_anchors", ["character_id"]
        )

    if not _has_table(conn, "scene_reasoning_contracts"):
        op.create_table(
            "scene_reasoning_contracts",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
            sa.Column(
                "chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id"), nullable=False
            ),
            sa.Column("scene_no", sa.Integer(), nullable=False),
            sa.Column("contract_json", JSONB(), nullable=False),
            sa.Column("contract_hash", sa.String(64), nullable=False),
            sa.Column("source_run_id", UUID(as_uuid=True), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
            sa.Column("validation_json", JSONB(), nullable=False, server_default="{}"),
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
            sa.UniqueConstraint("chapter_id", "scene_no", "contract_hash"),
        )
        op.create_index(
            "idx_scene_contract_book_chapter",
            "scene_reasoning_contracts",
            ["book_id", "chapter_id"],
        )
        op.create_index(
            "ix_scene_reasoning_contracts_book_id", "scene_reasoning_contracts", ["book_id"]
        )
        op.create_index(
            "ix_scene_reasoning_contracts_chapter_id", "scene_reasoning_contracts", ["chapter_id"]
        )

    if not _has_table(conn, "story_event_edges"):
        op.create_table(
            "story_event_edges",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False),
            sa.Column(
                "chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id"), nullable=False
            ),
            sa.Column(
                "source_event_id",
                UUID(as_uuid=True),
                sa.ForeignKey("story_events.id"),
                nullable=False,
            ),
            sa.Column(
                "target_event_id",
                UUID(as_uuid=True),
                sa.ForeignKey("story_events.id"),
                nullable=False,
            ),
            sa.Column("relation_type", sa.String(40), nullable=False),
            sa.Column("edge_mode", sa.String(16), nullable=False),
            sa.Column("mechanism", sa.Text(), nullable=True),
            sa.Column("strength", sa.Float(), nullable=True),
            sa.Column("source_contract_id", UUID(as_uuid=True), nullable=True),
            sa.Column("source_run_id", UUID(as_uuid=True), nullable=True),
            sa.Column("evidence", JSONB(), nullable=False, server_default="{}"),
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
        op.create_index(
            "idx_story_event_edges_source", "story_event_edges", ["book_id", "source_event_id"]
        )
        op.create_index(
            "idx_story_event_edges_target", "story_event_edges", ["book_id", "target_event_id"]
        )
        op.create_index("ix_story_event_edges_book_id", "story_event_edges", ["book_id"])
        op.create_index("ix_story_event_edges_chapter_id", "story_event_edges", ["chapter_id"])
        op.create_index(
            "ix_story_event_edges_source_event_id", "story_event_edges", ["source_event_id"]
        )
        op.create_index(
            "ix_story_event_edges_target_event_id", "story_event_edges", ["target_event_id"]
        )


def downgrade() -> None:
    op.drop_index("ix_story_event_edges_target_event_id", table_name="story_event_edges")
    op.drop_index("ix_story_event_edges_source_event_id", table_name="story_event_edges")
    op.drop_index("ix_story_event_edges_chapter_id", table_name="story_event_edges")
    op.drop_index("ix_story_event_edges_book_id", table_name="story_event_edges")
    op.drop_index("idx_story_event_edges_target", table_name="story_event_edges")
    op.drop_index("idx_story_event_edges_source", table_name="story_event_edges")
    op.drop_table("story_event_edges")
    op.drop_index("ix_scene_reasoning_contracts_chapter_id", table_name="scene_reasoning_contracts")
    op.drop_index("ix_scene_reasoning_contracts_book_id", table_name="scene_reasoning_contracts")
    op.drop_index(
        "idx_scene_contract_book_chapter", table_name="scene_reasoning_contracts"
    )
    op.drop_table("scene_reasoning_contracts")
    op.drop_index("ix_character_core_anchors_character_id", table_name="character_core_anchors")
    op.drop_index("ix_character_core_anchors_book_id", table_name="character_core_anchors")
    op.drop_index("idx_core_anchor_book_char", table_name="character_core_anchors")
    op.drop_table("character_core_anchors")
