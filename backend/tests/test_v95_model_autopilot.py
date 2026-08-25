"""v9.5 P0 tests: model autopilot pure logic + routing/core guard tests.

Pure-logic cases run anywhere; DB-backed cases auto-skip without PostgreSQL.
"""
from __future__ import annotations

import pytest

from app.model_autopilot.capability import (
    CONTEXT_REQUIRED_ROLES,
    context_fit_score,
    required_context_for,
)
from app.model_autopilot.health import classify_health
from app.model_autopilot.router import default_policy_for
from app.model_autopilot.scoring import ROOT_CAUSE_ROLES, _human_weight
from app.model_autopilot.seed import (
    FAMILY_QUALITY_TIER,
    MODEL_CAPABILITY_SEED,
    seed_for_model,
    static_quality_score_for,
)


# ── spec §33 Context fit ──


def test_context_fit_utilization_bands():
    assert context_fit_score(200000, 100000) == 100  # 50%
    assert context_fit_score(200000, 140000) == 90   # 70%
    assert context_fit_score(200000, 170000) == 70   # 85%
    assert context_fit_score(200000, 190000) == 40   # 95%
    assert context_fit_score(200000, 199000) is None  # >95% reject


def test_context_fit_insufficient_or_unknown():
    assert context_fit_score(128000, 150000) is None  # insufficient
    assert context_fit_score(None, 150000) is None    # unknown → reject for key roles


def test_required_context_includes_margin():
    # margin = max(4096, 5% of window) with window 0 → 4096
    assert required_context_for(60000, 12000, 0) == 60000 + 12000 + 4096


def test_key_roles_require_context():
    assert "draft_writer" in CONTEXT_REQUIRED_ROLES
    assert "chapter_planner" in CONTEXT_REQUIRED_ROLES
    assert "review_agent" in CONTEXT_REQUIRED_ROLES
    assert "state_extractor" in CONTEXT_REQUIRED_ROLES


# ── spec §10 seed ──


def test_seed_covers_known_models():
    assert seed_for_model("gpt-4o")["context_window"] == 128000
    assert seed_for_model("deepseek-reasoner")["supports_reasoning"] is True
    # family fallback without guessing from arbitrary names
    assert seed_for_model("claude-sonnet-4-20250514")["quality_tier"] in {"S", "A"}


def test_static_quality_tier_scale():
    assert static_quality_score_for("gpt-4o") == 90.0  # tier A
    assert static_quality_score_for("o3") == 95.0      # tier S
    assert static_quality_score_for("unknown-future-model") is None


# ── spec §18 human weight bands ──


def test_human_quality_weight_bands():
    assert _human_weight(5) == 0.0
    assert _human_weight(10) == 0.15
    assert _human_weight(30) == 0.30
    assert _human_weight(200) == 0.40


# ── spec §24 health classification ──


def test_health_classification():
    assert classify_health(
        probe_ok_recent=1.0, prod_15m=0.98, consecutive_failures=0,
        last_probe_status="ok", last_error=None, has_valid_probe=True,
    ) == "healthy"
    assert classify_health(
        probe_ok_recent=1.0, prod_15m=0.80, consecutive_failures=0,
        last_probe_status="ok", last_error=None, has_valid_probe=True,
    ) == "degraded"
    assert classify_health(
        probe_ok_recent=0.0, prod_15m=None, consecutive_failures=3,
        last_probe_status="failed", last_error="HTTP_503", has_valid_probe=True,
    ) == "unavailable"
    assert classify_health(
        probe_ok_recent=None, prod_15m=None, consecutive_failures=0,
        last_probe_status=None, last_error=None, has_valid_probe=False,
    ) == "unknown"
    assert classify_health(
        probe_ok_recent=0.0, prod_15m=0.4, consecutive_failures=2,
        last_probe_status="failed", last_error="MODEL_NOT_FOUND", has_valid_probe=True,
    ) == "unavailable"


# ── spec §35 default weights ──


def test_default_policy_weights():
    p = default_policy_for()
    assert p["quality_weight"] == 0.45
    assert p["reliability_weight"] == 0.20
    assert p["context_weight"] == 0.15
    assert p["health_weight"] == 0.10
    assert "performance_weight" in p
    assert p["latency_weight"] == 0
    assert p["cost_weight"] == 0
    assert p["fallback_count"] == 2
    assert p["require_provider_diversity"] is True


def test_default_policy_mode():
    assert default_policy_for("manual")["mode"] == "manual"
    assert default_policy_for("auto")["mode"] == "auto"
    assert default_policy_for("unknown-mode")["mode"] == "hybrid"


# ── spec §54 root cause mapping ──


def test_root_cause_roles_mapping():
    assert ROOT_CAUSE_ROLES["chapter_planner"] == "chapter_planner"
    assert ROOT_CAUSE_ROLES["draft_writer"] == "draft_writer"
    assert ROOT_CAUSE_ROLES["reviewer"] == "review_agent"
    assert "review_agent" in ROOT_CAUSE_ROLES.values()
