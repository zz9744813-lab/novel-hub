"""v9.5: model autopilot data layer (7 tables + 2 extensions).

model_catalog / model_capability_profiles / model_health_probes /
model_health_snapshots / model_role_scores / model_routing_policies /
model_route_plans; agent_model_bindings routing fields;
writing_sessions preflight columns. Spec §5–§12, §22–§23, §34, §43, §45.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0018_model_autopilot"
down_revision = "0017_writing_sessions"
branch_labels = None
depends_on = None


def _ts():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    ]


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

    # ── model_catalog (spec §6) ──
    if not _has_table(conn, "model_catalog"):
        op.create_table(
            "model_catalog",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("provider", sa.String(100), nullable=False, index=True),
            sa.Column("model_id", sa.String(200), nullable=False, index=True),
            sa.Column("display_name", sa.String(300), nullable=True),
            sa.Column("family", sa.String(60), nullable=True),
            sa.Column("vendor", sa.String(100), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("auto_route_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("availability_status", sa.String(20), nullable=False, server_default="unknown"),
            sa.Column("discovery_source", sa.String(30), nullable=False, server_default="seed"),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("metadata_json", JSONB(), nullable=False, server_default="{}"),
            *_ts(),
        )
        op.create_unique_constraint("uq_model_catalog_provider_model", "model_catalog", ["provider", "model_id"])

    # ── model_capability_profiles (spec §7) ──
    if not _has_table(conn, "model_capability_profiles"):
        op.create_table(
            "model_capability_profiles",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("model_catalog_id", UUID(as_uuid=True), sa.ForeignKey("model_catalog.id"), nullable=False, index=True),
            sa.Column("context_window", sa.Integer(), nullable=True),
            sa.Column("max_output_tokens", sa.Integer(), nullable=True),
            sa.Column("supports_stream", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("supports_json_schema", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("supports_tools", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("supports_reasoning", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("supports_system_prompt", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("reasoning_mode", sa.String(30), nullable=True),
            sa.Column("capability_source", sa.String(30), nullable=False, server_default="unknown"),
            sa.Column("capability_confidence", sa.Float(), nullable=True),
            sa.Column("quality_tier", sa.String(12), nullable=False, server_default="unknown"),
            sa.Column("static_quality_score", sa.Float(), nullable=True),
            sa.Column("metadata_json", JSONB(), nullable=False, server_default="{}"),
            *_ts(),
        )

    # ── model_health_probes (spec §22) ──
    if not _has_table(conn, "model_health_probes"):
        op.create_table(
            "model_health_probes",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("model_catalog_id", UUID(as_uuid=True), sa.ForeignKey("model_catalog.id"), nullable=False, index=True),
            sa.Column("probe_type", sa.String(12), nullable=False),  # l0_provider|l1_ping|l2_capability
            sa.Column("status", sa.String(30), nullable=False, index=True),  # ok|failed
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("first_token_ms", sa.Integer(), nullable=True),
            sa.Column("http_status", sa.Integer(), nullable=True),
            sa.Column("error_code", sa.String(60), nullable=True),
            sa.Column("output_valid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("detail_json", JSONB(), nullable=False, server_default="{}"),
            *_ts(),
        )

    # ── model_health_snapshots (spec §23) ──
    if not _has_table(conn, "model_health_snapshots"):
        op.create_table(
            "model_health_snapshots",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("model_catalog_id", UUID(as_uuid=True), sa.ForeignKey("model_catalog.id"), nullable=False, unique=True),
            sa.Column("health_status", sa.String(16), nullable=False, server_default="unknown", index=True),
            sa.Column("success_rate_15m", sa.Float(), nullable=True),
            sa.Column("success_rate_1h", sa.Float(), nullable=True),
            sa.Column("success_rate_24h", sa.Float(), nullable=True),
            sa.Column("p50_latency_ms", sa.Integer(), nullable=True),
            sa.Column("p95_latency_ms", sa.Integer(), nullable=True),
            sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_mix_json", JSONB(), nullable=False, server_default="{}"),
            sa.Column("health_score", sa.Float(), nullable=True),
            *_ts(),
        )

    # ── model_role_scores (spec §12) ──
    if not _has_table(conn, "model_role_scores"):
        op.create_table(
            "model_role_scores",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("model_catalog_id", UUID(as_uuid=True), sa.ForeignKey("model_catalog.id"), nullable=False, index=True),
            sa.Column("agent_role", sa.String(100), nullable=False, index=True),
            sa.Column("static_prior_score", sa.Float(), nullable=True),
            sa.Column("benchmark_score", sa.Float(), nullable=True),
            sa.Column("production_quality_score", sa.Float(), nullable=True),
            sa.Column("human_quality_score", sa.Float(), nullable=True),
            sa.Column("reliability_score", sa.Float(), nullable=True),
            sa.Column("composite_score", sa.Float(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("score_version", sa.String(30), nullable=False, server_default="v1"),
            sa.Column("detail_json", JSONB(), nullable=False, server_default="{}"),
            *_ts(),
        )
        op.create_unique_constraint(
            "uq_model_role_scores_catalog_role", "model_role_scores", ["model_catalog_id", "agent_role"]
        )

    # ── model_routing_policies (spec §34) ──
    if not _has_table(conn, "model_routing_policies"):
        op.create_table(
            "model_routing_policies",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("scope_type", sa.String(20), nullable=False, server_default="global"),
            sa.Column("scope_id", UUID(as_uuid=True), nullable=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("mode", sa.String(12), nullable=False, server_default="hybrid"),
            sa.Column("min_quality_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("min_health_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("require_provider_diversity", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("fallback_count", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("allow_degraded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("quality_weight", sa.Float(), nullable=False, server_default="0.45"),
            sa.Column("reliability_weight", sa.Float(), nullable=False, server_default="0.25"),
            sa.Column("context_weight", sa.Float(), nullable=False, server_default="0.20"),
            sa.Column("health_weight", sa.Float(), nullable=False, server_default="0.10"),
            sa.Column("latency_weight", sa.Float(), nullable=False, server_default="0"),
            sa.Column("cost_weight", sa.Float(), nullable=False, server_default="0"),
            sa.Column("role_overrides_json", JSONB(), nullable=False, server_default="{}"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            *_ts(),
        )

    # ── model_route_plans (spec §43) ──
    if not _has_table(conn, "model_route_plans"):
        op.create_table(
            "model_route_plans",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("book_id", UUID(as_uuid=True), sa.ForeignKey("books.id"), nullable=False, index=True),
            sa.Column("writing_session_id", UUID(as_uuid=True), sa.ForeignKey("writing_sessions.id"), nullable=True, index=True),
            sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("policy_id", UUID(as_uuid=True), nullable=True),
            sa.Column("policy_version", sa.Integer(), nullable=True),
            sa.Column("assignments_json", JSONB(), nullable=False, server_default="{}"),
            sa.Column("health_snapshot_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reason_json", JSONB(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            *_ts(),
        )

    # ── agent_model_bindings extension (spec §3) ──
    cols = [c["name"] for c in sa.inspect(conn).get_columns("agent_model_bindings")]
    if "routing_mode" not in cols:
        op.add_column("agent_model_bindings", sa.Column("routing_mode", sa.String(12), nullable=False, server_default="hybrid"))
    if "routing_policy_id" not in cols:
        op.add_column("agent_model_bindings", sa.Column("routing_policy_id", UUID(as_uuid=True), nullable=True))
    if "manual_primary_locked" not in cols:
        op.add_column("agent_model_bindings", sa.Column("manual_primary_locked", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    if "manual_fallback_locked" not in cols:
        op.add_column("agent_model_bindings", sa.Column("manual_fallback_locked", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    if "allowed_model_ids" not in cols:
        op.add_column("agent_model_bindings", sa.Column("allowed_model_ids", JSONB(), nullable=False, server_default="[]"))
    if "blocked_model_ids" not in cols:
        op.add_column("agent_model_bindings", sa.Column("blocked_model_ids", JSONB(), nullable=False, server_default="[]"))

    # ── writing_sessions extension (spec §45) ──
    wcols = [c["name"] for c in sa.inspect(conn).get_columns("writing_sessions")]
    if "model_route_plan_id" not in wcols:
        op.add_column("writing_sessions", sa.Column("model_route_plan_id", UUID(as_uuid=True), nullable=True))
    if "model_routing_policy_version" not in wcols:
        op.add_column("writing_sessions", sa.Column("model_routing_policy_version", sa.Integer(), nullable=True))
    if "model_preflight_status" not in wcols:
        op.add_column("writing_sessions", sa.Column("model_preflight_status", sa.String(20), nullable=True))
    if "model_preflight_detail" not in wcols:
        op.add_column("writing_sessions", sa.Column("model_preflight_detail", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_table("model_route_plans")
    op.drop_table("model_routing_policies")
    op.drop_table("model_role_scores")
    op.drop_table("model_health_snapshots")
    op.drop_table("model_health_probes")
    op.drop_table("model_capability_profiles")
    op.drop_table("model_catalog")
    for col, coltype in [
        ("blocked_model_ids", JSONB()),
        ("allowed_model_ids", JSONB()),
        ("manual_fallback_locked", sa.Boolean()),
        ("manual_primary_locked", sa.Boolean()),
        ("routing_policy_id", UUID(as_uuid=True)),
        ("routing_mode", sa.String(12)),
    ]:
        op.drop_column("agent_model_bindings", col)
    for col in ["model_preflight_detail", "model_preflight_status", "model_routing_policy_version", "model_route_plan_id"]:
        op.drop_column("writing_sessions", col)
