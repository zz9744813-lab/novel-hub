"""Release diagnostics expose bounded model ids without provider metadata."""

from types import SimpleNamespace

from app.model_autopilot.preflight import _diagnostic_model_inventory


def _catalog(model: str, *, eligible: bool = False):
    return SimpleNamespace(
        provider="new-api",
        model_id=model,
        model_kind="text_generation" if eligible else "unknown",
        text_generation_eligible=eligible,
        auto_route_enabled=eligible,
        availability_status="available",
        metadata_json={"api_key": "must-not-leak"},
    )


def test_inventory_includes_candidate_families_and_never_metadata():
    report = _diagnostic_model_inventory(
        [_catalog("image-only"), _catalog("glm-5.2"), _catalog("custom-text", eligible=True)]
    )

    assert [row["model"] for row in report["models"]] == ["custom-text", "glm-5.2"]
    assert "must-not-leak" not in str(report)
    assert report["truncated"] is False


def test_inventory_is_bounded_and_marks_truncation():
    report = _diagnostic_model_inventory(
        [_catalog(f"qwen-{index}") for index in range(3)],
        limit=2,
    )

    assert len(report["models"]) == 2
    assert report["truncated"] is True
