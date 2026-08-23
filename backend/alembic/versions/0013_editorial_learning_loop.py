"""v9.3: Editorial Learning Loop tables + ChapterVersion lineage + Chapter.editorial_status.

Spec §79: 10 new tables. Numbering adapted to this repo (latest is 0012);
the spec's "0015" assumed intermediate v9.2 migrations that do not exist here.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0013_editorial_learning_loop"
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


def _has_column(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_table(conn, "editorial_review_policies"):
        op.create_table(
            "editorial_review_policies",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False, unique=True),
            sa.Column("mode", sa.String(20), nullable=False, server_default="windowed"),
            sa.Column("max_unreviewed_ahead", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("review_sampling_mode", sa.String(20), nullable=False, server_default="all"),
            sa.Column("require_review", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("good_score_threshold", sa.Integer(), nullable=False, server_default="85"),
            sa.Column("auto_pause_good_rate_threshold", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("auto_pause_consecutive_bad", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("rubric_template_id", UUID(as_uuid=True), nullable=True),
            sa.Column("experience_auto_activation", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("low_risk_auto_promote", sa.Boolean(), nullable=False, server_default="false"),
        )

    if not _has_table(conn, "editorial_rubric_templates"):
        op.create_table(
            "editorial_rubric_templates",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("dimensions", JSONB(), nullable=False, server_default="[]"),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
            sa.UniqueConstraint("book_id", "name"),
        )

    if not _has_table(conn, "editorial_review_rounds"):
        op.create_table(
            "editorial_review_rounds",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), nullable=False),
            sa.Column("chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id"), nullable=False),
            sa.Column("chapter_version_id", UUID(as_uuid=True), sa.ForeignKey("chapter_versions.id"), nullable=False),
            sa.Column("round_no", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
            sa.Column("verdict", sa.String(30), nullable=True),
            sa.Column("score_total", sa.Integer(), nullable=True),
            sa.Column("grade", sa.String(2), nullable=True),
            sa.Column("rubric_template_id", UUID(as_uuid=True), nullable=True),
            sa.Column("rubric_scores_json", JSONB(), nullable=True),
            sa.Column("overall_comment", sa.Text(), nullable=True),
            sa.Column("reviewer_kind", sa.String(20), nullable=False, server_default="human"),
            sa.Column("reviewer_id", sa.String(100), nullable=True),
            sa.Column("ai_issue_dispositions", JSONB(), nullable=False, server_default="{}"),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("chapter_id", "round_no"),
        )
        op.create_index("ix_err_book", "editorial_review_rounds", ["book_id"])

    if not _has_table(conn, "editorial_annotations"):
        op.create_table(
            "editorial_annotations",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("review_round_id", UUID(as_uuid=True),
                      sa.ForeignKey("editorial_review_rounds.id", ondelete="CASCADE"), nullable=False),
            sa.Column("book_id", UUID(as_uuid=True), nullable=False),
            sa.Column("chapter_id", UUID(as_uuid=True), nullable=False),
            sa.Column("chapter_version_id", UUID(as_uuid=True), nullable=False),
            sa.Column("annotation_type", sa.String(30), nullable=False),
            sa.Column("category", sa.String(50), nullable=True),
            sa.Column("severity", sa.String(20), nullable=False, server_default="minor"),
            sa.Column("scope", sa.String(20), nullable=False, server_default="local_span"),
            sa.Column("scene_no", sa.Integer(), nullable=True),
            sa.Column("paragraph_key", sa.String(50), nullable=True),
            sa.Column("start_offset", sa.Integer(), nullable=True),
            sa.Column("end_offset", sa.Integer(), nullable=True),
            sa.Column("quoted_text", sa.Text(), nullable=True),
            sa.Column("quote_hash", sa.String(64), nullable=True),
            sa.Column("context_before", sa.Text(), nullable=True),
            sa.Column("context_after", sa.Text(), nullable=True),
            sa.Column("context_hash", sa.String(64), nullable=True),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("suggested_text", sa.Text(), nullable=True),
            sa.Column("is_blocking", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("ai_issue_match_ids", JSONB(), nullable=False, server_default="[]"),
            sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
            sa.Column("resolution_status", sa.String(20), nullable=False, server_default="open"),
            sa.Column("resolved_by_version_id", UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_ea_round", "editorial_annotations", ["review_round_id"])
        op.create_index("ix_ea_book", "editorial_annotations", ["book_id"])

    if not _has_table(conn, "editorial_feedback_insights"):
        op.create_table(
            "editorial_feedback_insights",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("annotation_id", UUID(as_uuid=True),
                      sa.ForeignKey("editorial_annotations.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("book_id", UUID(as_uuid=True), nullable=False),
            sa.Column("normalized_category", sa.String(50), nullable=False),
            sa.Column("human_intent", sa.Text(), nullable=True),
            sa.Column("symptom", sa.Text(), nullable=True),
            sa.Column("root_cause_component", sa.String(50), nullable=False),
            sa.Column("secondary_components", JSONB(), nullable=False, server_default="[]"),
            sa.Column("remediation_level", sa.String(30), nullable=False, server_default="L1"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("evidence_refs", JSONB(), nullable=False, server_default="[]"),
            sa.Column("analysis_run_id", UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_efi_book", "editorial_feedback_insights", ["book_id"])

    if not _has_table(conn, "editorial_experience_cards"):
        op.create_table(
            "editorial_experience_cards",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), nullable=True),
            sa.Column("rule_type", sa.String(30), nullable=False),
            sa.Column("scope_type", sa.String(20), nullable=False, server_default="book"),
            sa.Column("scope_ref", JSONB(), nullable=False, server_default="{}"),
            sa.Column("category", sa.String(50), nullable=False),
            sa.Column("trigger_conditions", JSONB(), nullable=False, server_default="{}"),
            sa.Column("instruction", sa.Text(), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column("avoid_when", JSONB(), nullable=False, server_default="[]"),
            sa.Column("target_components", JSONB(), nullable=False, server_default="[]"),
            sa.Column("positive_example_refs", JSONB(), nullable=False, server_default="[]"),
            sa.Column("negative_example_refs", JSONB(), nullable=False, server_default="[]"),
            sa.Column("support_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("contradiction_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("status", sa.String(20), nullable=False, server_default="candidate"),
            sa.Column("is_locked", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("effective_from_chapter", sa.Integer(), nullable=True),
            sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("source_annotation_ids", JSONB(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_eec_book", "editorial_experience_cards", ["book_id"])
        op.create_index("ix_eec_category", "editorial_experience_cards", ["category"])

    if not _has_table(conn, "editorial_preference_pairs"):
        op.create_table(
            "editorial_preference_pairs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), nullable=False),
            sa.Column("chapter_id", UUID(as_uuid=True), nullable=False),
            sa.Column("review_round_id", UUID(as_uuid=True), nullable=True),
            sa.Column("annotation_id", UUID(as_uuid=True), nullable=True),
            sa.Column("context_package_ref", JSONB(), nullable=True),
            sa.Column("scene_contract_ref", JSONB(), nullable=True),
            sa.Column("style_contract_ref", JSONB(), nullable=True),
            sa.Column("rejected_text", sa.Text(), nullable=False),
            sa.Column("chosen_text", sa.Text(), nullable=False),
            sa.Column("preference_reason", sa.Text(), nullable=True),
            sa.Column("category", sa.String(50), nullable=True),
            sa.Column("scope", sa.String(20), nullable=False, server_default="local_span"),
            sa.Column("source", sa.String(30), nullable=False, server_default="human_direct_edit"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_epp_book", "editorial_preference_pairs", ["book_id"])

    if not _has_table(conn, "editorial_improvement_proposals"):
        op.create_table(
            "editorial_improvement_proposals",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), nullable=True),
            sa.Column("proposal_type", sa.String(50), nullable=False),
            sa.Column("target_component", sa.String(50), nullable=False),
            sa.Column("target_scope", sa.String(20), nullable=False, server_default="book"),
            sa.Column("current_version_ref", JSONB(), nullable=True),
            sa.Column("candidate_patch", JSONB(), nullable=False, server_default="{}"),
            sa.Column("risk_level", sa.String(10), nullable=False, server_default="low"),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("supporting_experience_ids", JSONB(), nullable=False, server_default="[]"),
            sa.Column("supporting_review_ids", JSONB(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
            sa.Column("created_by_run_id", UUID(as_uuid=True), nullable=True),
            sa.Column("approved_by", sa.String(100), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("experiment_id", UUID(as_uuid=True), nullable=True),
            sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("effective_from_chapter", sa.Integer(), nullable=True),
            sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_eip_book", "editorial_improvement_proposals", ["book_id"])

    if not _has_table(conn, "editorial_regression_cases"):
        op.create_table(
            "editorial_regression_cases",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), nullable=False),
            sa.Column("source_review_round_id", UUID(as_uuid=True), nullable=True),
            sa.Column("chapter_version_id", UUID(as_uuid=True), nullable=False),
            sa.Column("scene_no", sa.Integer(), nullable=True),
            sa.Column("case_type", sa.String(30), nullable=False, server_default="chapter_review"),
            sa.Column("target_component", sa.String(50), nullable=False, server_default="review_agent"),
            sa.Column("context_package_refs", JSONB(), nullable=False, server_default="[]"),
            sa.Column("prompt_version_ref", JSONB(), nullable=True),
            sa.Column("model_binding_snapshot", JSONB(), nullable=True),
            sa.Column("scene_contract_ref", JSONB(), nullable=True),
            sa.Column("style_contract_ref", JSONB(), nullable=True),
            sa.Column("chapter_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("human_verdict", sa.String(30), nullable=True),
            sa.Column("rubric_scores", JSONB(), nullable=True),
            sa.Column("human_annotation_ids", JSONB(), nullable=False, server_default="[]"),
            sa.Column("expected_properties", JSONB(), nullable=False, server_default="[]"),
            sa.Column("forbidden_properties", JSONB(), nullable=False, server_default="[]"),
            sa.Column("difficulty", sa.String(20), nullable=False, server_default="normal"),
            sa.Column("scene_type", sa.String(50), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_erc_book", "editorial_regression_cases", ["book_id"])

    if not _has_table(conn, "editorial_experiments"):
        op.create_table(
            "editorial_experiments",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), nullable=True),
            sa.Column("proposal_id", UUID(as_uuid=True), nullable=True),
            sa.Column("baseline_version", sa.String(100), nullable=False),
            sa.Column("candidate_version", sa.String(100), nullable=False),
            sa.Column("case_ids", JSONB(), nullable=False, server_default="[]"),
            sa.Column("metrics_baseline", JSONB(), nullable=False, server_default="{}"),
            sa.Column("metrics_candidate", JSONB(), nullable=False, server_default="{}"),
            sa.Column("delta_metrics", JSONB(), nullable=False, server_default="{}"),
            sa.Column("hard_gate_results", JSONB(), nullable=False, server_default="{}"),
            sa.Column("pareto_candidates", JSONB(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(20), nullable=False, server_default="running"),
            sa.Column("recommendation", sa.String(20), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_ee_book", "editorial_experiments", ["book_id"])

    # ── Chapter.editorial_status (spec §4) ─────────────────────────────
    if not _has_column(conn, "chapters", "editorial_status"):
        op.add_column(
            "chapters",
            sa.Column("editorial_status", sa.String(30), nullable=False,
                      server_default="pending_review"),
        )
        op.create_index("ix_chapters_editorial_status", "chapters", ["editorial_status"])

    # ── ChapterVersion lineage (spec §31) ──────────────────────────────
    if not _has_column(conn, "chapter_versions", "parent_version_id"):
        op.add_column("chapter_versions", sa.Column("parent_version_id", UUID(as_uuid=True), nullable=True))
    if not _has_column(conn, "chapter_versions", "editorial_review_round_id"):
        op.add_column("chapter_versions", sa.Column("editorial_review_round_id", UUID(as_uuid=True), nullable=True))
    if not _has_column(conn, "chapter_versions", "revision_origin"):
        op.add_column("chapter_versions", sa.Column("revision_origin", sa.String(40), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    for table in (
        "editorial_experiments",
        "editorial_regression_cases",
        "editorial_improvement_proposals",
        "editorial_preference_pairs",
        "editorial_experience_cards",
        "editorial_feedback_insights",
        "editorial_annotations",
        "editorial_review_rounds",
        "editorial_rubric_templates",
        "editorial_review_policies",
    ):
        if _has_table(conn, table):
            op.drop_table(table)
    if _has_column(conn, "chapter_versions", "revision_origin"):
        op.drop_column("chapter_versions", "revision_origin")
    if _has_column(conn, "chapter_versions", "editorial_review_round_id"):
        op.drop_column("chapter_versions", "editorial_review_round_id")
    if _has_column(conn, "chapter_versions", "parent_version_id"):
        op.drop_column("chapter_versions", "parent_version_id")
    if _has_column(conn, "chapters", "editorial_status"):
        op.drop_column("chapters", "editorial_status")
