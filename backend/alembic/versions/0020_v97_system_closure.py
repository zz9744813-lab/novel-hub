"""v9.7: system closure — quality signals, prompt evolution, evaluation bank,
effective-context measurement, AI-Tone, technique intelligence, provenance.

One migration for the whole v9.7 data layer (spec §31).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0020_v97_system_closure"
down_revision = "0019_model_setup_and_metrics"
branch_labels = None
depends_on = None


def _cols(conn, table):
    return [c["name"] for c in sa.inspect(conn).get_columns(table)]


def _has_table(conn, table):
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

    # ── quality_signals (spec §14) ──
    if not _has_table(conn, "quality_signals"):
        op.create_table(
            "quality_signals",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), nullable=False, index=True),
            sa.Column("chapter_id", UUID(as_uuid=True), nullable=True),
            sa.Column("chapter_run_id", UUID(as_uuid=True), nullable=True),
            sa.Column("agent_run_id", UUID(as_uuid=True), nullable=True),
            sa.Column("agent_role", sa.String(100), nullable=True),
            sa.Column("signal_type", sa.String(40), nullable=False),  # human_review|review_agent|style_verifier|ai_tone_lint|ccne|drift_audit|state_extractor|model_gateway|prompt_experiment
            sa.Column("metric_name", sa.String(80), nullable=False),
            sa.Column("numeric_value", sa.Float(), nullable=True),
            sa.Column("label", sa.String(200), nullable=True),
            sa.Column("severity", sa.String(16), nullable=True),
            sa.Column("source", sa.String(60), nullable=True),
            sa.Column("source_ref", sa.String(200), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            *_ts(),
        )
        op.create_index("ix_quality_signals_book_role", "quality_signals", ["book_id", "agent_role", "created_at"])
        op.create_index("ix_quality_signals_book_metric", "quality_signals", ["book_id", "metric_name", "created_at"])

    # ── prompt evolution (spec §7) ──
    for table, extras in [
        ("prompt_evolution_runs", [sa.Column("book_id", UUID(as_uuid=True), nullable=False, index=True),
                                   sa.Column("target_role", sa.String(100), nullable=False),
                                   sa.Column("trigger_code", sa.String(60), nullable=True),
                                   sa.Column("trigger_detail", JSONB(), nullable=True),
                                   sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
                                   sa.Column("winner_candidate_id", UUID(as_uuid=True), nullable=True),
                                   sa.Column("result_json", JSONB(), nullable=True)]),
        ("prompt_evolution_candidates", [sa.Column("run_id", UUID(as_uuid=True), nullable=False, index=True),
                                         sa.Column("candidate_version", sa.Integer(), nullable=False, server_default="1"),
                                         sa.Column("system_prompt", sa.Text(), nullable=False),
                                         sa.Column("user_prompt_template", sa.Text(), nullable=True),
                                         sa.Column("context_policy_json", JSONB(), nullable=False, server_default="{}"),
                                         sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
                                         sa.Column("result_json", JSONB(), nullable=True)]),
    ]:
        if not _has_table(conn, table):
            op.create_table(table, sa.Column("id", UUID(as_uuid=True), primary_key=True), *_ts(), *extras)

    # ── technique intelligence (spec §23) ──
    # technique_cards already exists (v7 era); extend with v9.7 DeepStudy columns
    tcols = _cols(conn, "technique_cards")
    if "book_id" not in tcols:
        op.add_column(
            "technique_cards",
            sa.Column(
                "book_id",
                UUID(as_uuid=True),
                sa.ForeignKey("books.id"),
                nullable=True,
            ),
        )
        tcols.append("book_id")
    for col, type_, default in [
        ("technique_type", sa.String(40), "dialogue"),
        ("mechanism", sa.Text(), None),
        ("trigger_conditions", JSONB(), "[]"),
        ("applicable_scene_types", JSONB(), "[]"),
        ("avoid_when", JSONB(), "[]"),
        ("planning_instruction", sa.Text(), None),
        ("draft_instruction", sa.Text(), None),
        ("expected_effect", sa.Text(), None),
        ("support_count", sa.Integer(), "0"),
        ("contradiction_count", sa.Integer(), "0"),
        ("confidence", sa.Float(), None),
        ("status", sa.String(20), "candidate"),
    ]:
        if col not in tcols:
            op.add_column("technique_cards", sa.Column(col, type_, nullable=True, server_default=default))
    op.create_index("ix_technique_cards_book_status", "technique_cards", ["book_id", "status", "technique_type"])

    if not _has_table(conn, "technique_card_usages"):
        op.create_table(
            "technique_card_usages",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("technique_card_id", UUID(as_uuid=True), nullable=False, index=True),
            sa.Column("book_id", UUID(as_uuid=True), nullable=False),
            sa.Column("chapter_id", UUID(as_uuid=True), nullable=True),
            sa.Column("scene_id", UUID(as_uuid=True), nullable=True),
            sa.Column("agent_run_id", UUID(as_uuid=True), nullable=True),
            sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("effective", sa.Boolean(), nullable=True),
            *_ts(),
        )

    # ── AI-Tone (spec §25) ──
    if not _has_table(conn, "ai_tone_findings"):
        op.create_table(
            "ai_tone_findings",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), nullable=False, index=True),
            sa.Column("chapter_id", UUID(as_uuid=True), nullable=True, index=True),
            sa.Column("chapter_run_id", UUID(as_uuid=True), nullable=True),
            sa.Column("paragraph_key", sa.String(50), nullable=True),
            sa.Column("start", sa.Integer(), nullable=True),
            sa.Column("end", sa.Integer(), nullable=True),
            sa.Column("excerpt", sa.Text(), nullable=True),
            sa.Column("rule_id", sa.String(60), nullable=False),
            sa.Column("severity", sa.String(16), nullable=False, server_default="minor"),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("scene_type", sa.String(40), nullable=True),
            sa.Column("style_override", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("auto_patchable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("human_disposition", sa.String(16), nullable=True),  # confirmed|dismissed|corrected
            sa.Column("corrected_category", sa.String(40), nullable=True),
            *_ts(),
        )
        op.create_index("ix_ai_tone_findings_book_chapter", "ai_tone_findings", ["book_id", "chapter_id", "rule_id"])

    if not _has_table(conn, "ai_tone_rule_calibrations"):
        op.create_table(
            "ai_tone_rule_calibrations",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), nullable=True, index=True),
            sa.Column("rule_id", sa.String(60), nullable=False),
            sa.Column("confirmed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("dismissed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("corrected", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("precision", sa.Float(), nullable=True),
            sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
            *_ts(),
        )

    # ── model evaluation bank (spec §13.46) ──
    if not _has_table(conn, "model_eval_suites"):
        op.create_table(
            "model_eval_suites",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("suite_key", sa.String(80), nullable=False, index=True),
            sa.Column("version", sa.String(20), nullable=False),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("purpose", sa.Text(), nullable=True),
            sa.Column("target_role", sa.String(100), nullable=True),
            sa.Column("difficulty", sa.String(16), nullable=True),
            sa.Column("mode", sa.String(16), nullable=True),  # anchor|rotation|private|production
            sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("pass_threshold", sa.Float(), nullable=False, server_default="0.8"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            *_ts(),
        )

    if not _has_table(conn, "model_eval_cases"):
        op.create_table(
            "model_eval_cases",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("suite_id", UUID(as_uuid=True), nullable=False, index=True),
            sa.Column("case_key", sa.String(120), nullable=False),
            sa.Column("case_version", sa.String(20), nullable=False, server_default="1"),
            sa.Column("role", sa.String(100), nullable=True),
            sa.Column("category", sa.String(60), nullable=True),
            sa.Column("difficulty", sa.String(16), nullable=True),
            sa.Column("prompt_template", sa.Text(), nullable=False),
            sa.Column("context_template", sa.Text(), nullable=True),
            sa.Column("generator_type", sa.String(30), nullable=True),
            sa.Column("generator_config", JSONB(), nullable=True),
            sa.Column("expected_answer", sa.Text(), nullable=True),
            sa.Column("expected_schema", JSONB(), nullable=True),
            sa.Column("grader_type", sa.String(30), nullable=False),
            sa.Column("grader_config", JSONB(), nullable=True),
            sa.Column("max_output_tokens", sa.Integer(), nullable=False, server_default="1024"),
            sa.Column("temperature", sa.Float(), nullable=False, server_default="0"),
            sa.Column("private_case", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("case_hash", sa.String(64), nullable=True),
            *_ts(),
        )

    if not _has_table(conn, "model_eval_runs"):
        op.create_table(
            "model_eval_runs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("model_catalog_id", UUID(as_uuid=True), nullable=False, index=True),
            sa.Column("mode", sa.String(20), nullable=False, server_default="qualification"),
            sa.Column("status", sa.String(20), nullable=False, server_default="queued", index=True),
            sa.Column("benchmark_revision", sa.String(40), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("overall_score", sa.Float(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("result_summary", JSONB(), nullable=True),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            *_ts(),
        )
        op.create_index("ix_model_eval_runs_model_status", "model_eval_runs", ["model_catalog_id", "status"])

    if not _has_table(conn, "model_eval_case_results"):
        op.create_table(
            "model_eval_case_results",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("run_id", UUID(as_uuid=True), nullable=False, index=True),
            sa.Column("case_id", UUID(as_uuid=True), nullable=False),
            sa.Column("variant_seed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("context_target_tokens", sa.Integer(), nullable=True),
            sa.Column("provider_prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("response_hash", sa.String(64), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("passed", sa.Boolean(), nullable=True),
            sa.Column("grader_detail", JSONB(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("first_token_ms", sa.Integer(), nullable=True),
            sa.Column("completion_tokens", sa.Integer(), nullable=True),
            sa.Column("tokens_per_second", sa.Float(), nullable=True),
            sa.Column("error_code", sa.String(60), nullable=True),
            *_ts(),
        )

    if not _has_table(conn, "model_context_profiles"):
        op.create_table(
            "model_context_profiles",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("model_catalog_id", UUID(as_uuid=True), nullable=False, unique=True),
            sa.Column("declared_context_window", sa.Integer(), nullable=True),
            sa.Column("accepted_context_window", sa.Integer(), nullable=True),
            sa.Column("effective_context_window", sa.Integer(), nullable=True),
            sa.Column("position_robustness_score", sa.Float(), nullable=True),
            sa.Column("multi_hop_score", sa.Float(), nullable=True),
            sa.Column("instruction_retention_score", sa.Float(), nullable=True),
            sa.Column("belief_boundary_score", sa.Float(), nullable=True),
            sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("benchmark_revision", sa.String(40), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            *_ts(),
        )

    # ── model catalog classification (spec §13.47) ──
    mcols = _cols(conn, "model_catalog")
    for col, type_, default in [
        ("model_kind", sa.String(40), "unknown"),
        ("input_modalities", JSONB(), "[]"),
        ("output_modalities", JSONB(), "[]"),
        ("text_generation_eligible", sa.Boolean(), "false"),
        ("classification_source", sa.String(30), "unknown"),
        ("classification_confidence", sa.Float(), None),
        ("evaluation_status", sa.String(30), "unclassified"),
        ("certification_level", sa.String(30), "none"),
        ("certification_confidence", sa.Float(), None),
        ("benchmark_revision", sa.String(40), None),
        ("last_certified_at", sa.DateTime(timezone=True), None),
        ("evaluation_exclusion_reason", sa.String(200), None),
    ]:
        if col not in mcols:
            op.add_column("model_catalog", sa.Column(col, type_, nullable=True, server_default=default if default is not None else None))

    # ── capability: three context lengths (spec §13.22/§13.47) ──
    ccols = _cols(conn, "model_capability_profiles")
    for col, type_ in [
        ("declared_context_window", sa.Integer()),
        ("accepted_context_window", sa.Integer()),
        ("effective_context_window", sa.Integer()),
        ("context_measurement_confidence", sa.Float()),
    ]:
        if col not in ccols:
            op.add_column("model_capability_profiles", sa.Column(col, type_, nullable=True))

    # ── context packages: experience/technique refs (spec §5/§23) ──
    acols = _cols(conn, "agent_context_packages")
    if "experience_refs" not in acols:
        op.add_column("agent_context_packages", sa.Column("experience_refs", JSONB(), nullable=False, server_default="[]"))
    if "technique_refs" not in acols:
        op.add_column("agent_context_packages", sa.Column("technique_refs", JSONB(), nullable=False, server_default="[]"))

    # ── provenance (spec §24) ──
    rcols = _cols(conn, "reference_samples")
    for col, type_ in [
        ("source_kind", sa.String(30)),
        ("source_ref_json", JSONB()),
        ("source_url", sa.Text()),
        ("research_task_id", UUID(as_uuid=True)),
        ("research_document_id", UUID(as_uuid=True)),
        ("imported_at", sa.DateTime(timezone=True)),
    ]:
        if col not in rcols:
            op.add_column("reference_samples", sa.Column(col, type_, nullable=True))

    # ── prompt template versions: evolution lineage (spec §7.5) ──
    pcols = _cols(conn, "prompt_template_versions")
    for col, type_ in [
        ("proposal_id", UUID(as_uuid=True)),
        ("experiment_id", UUID(as_uuid=True)),
        ("canary_status", sa.String(20)),
        ("rolled_back_from_id", UUID(as_uuid=True)),
    ]:
        if col not in pcols:
            op.add_column("prompt_template_versions", sa.Column(col, type_, nullable=True))

    # one active version per agent_role + scope_type + scope_id (spec §6/§31)
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_prompt_template
        ON prompt_template_versions(agent_role, scope_type, COALESCE(scope_id, '00000000-0000-0000-0000-000000000000'), status)
        WHERE status = 'active'
        """
    )


def downgrade() -> None:
    for t in ["model_context_profiles", "model_eval_case_results", "model_eval_runs",
              "model_eval_cases", "model_eval_suites", "ai_tone_rule_calibrations",
              "ai_tone_findings", "technique_card_usages", "technique_cards",
              "prompt_evolution_candidates", "prompt_evolution_runs", "quality_signals"]:
        op.drop_table(t)
    op.execute("DROP INDEX IF EXISTS uq_one_active_prompt_template")
