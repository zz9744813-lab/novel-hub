"""P1 regression: review service errors must not be cached as succeeded
checkpoints (poisoned checkpoint), and the review step key version must have
been bumped past the poisoned `review:0:` era."""
from app.gateway.model_gateway import _generation_controls  # noqa: F401  (import sanity)
import inspect

import pytest

import app.engine.pipeline as pipeline_mod
from app.engine.step_runner import PermanentStepError, RetryableStepError


def test_review_service_error_is_classified_not_cached():
    """The shared review wrapper returns the payload unraised; classification
    happens in validate_review_output inside run_step. A service-error output
    raises there — run_step persists a failed step, never a succeeded
    checkpoint."""
    payload = pipeline_mod.review_result_payload(False, [{
        "issue_id": "review_service_failure",
        "severity": "critical",
        "category": "service_error",
        "message": "final_content_empty",
    }])
    with pytest.raises(RetryableStepError) as exc:
        pipeline_mod.validate_review_output(payload)
    assert exc.value.code == "review_service_failure"


def test_review_outline_missing_is_permanent_through_shared_path():
    """outline_missing must be permanent, which only holds when the review
    wrappers hand the raw payload to the validator instead of raising a
    generic retryable service error first."""
    payload = pipeline_mod.review_result_payload(False, [{
        "issue_id": "outline_missing",
        "severity": "critical",
        "category": "service_error",
        "message": "outline node missing",
    }])
    with pytest.raises(PermanentStepError) as exc:
        pipeline_mod.validate_review_output(payload)
    assert exc.value.code == "outline_missing"


def test_review_wrappers_share_one_payload_shape():
    """Initial review and re-review go through the same constructor, so both
    paths get identical validate/run_step treatment (real wrapper -> run_step
    -> DB persistence is covered by test_session_recovery_db.py)."""
    assert pipeline_mod.review_result_payload(True, None) == {"passed": True, "issues": []}
    assert pipeline_mod.review_result_payload(False, []) == {"passed": False, "issues": []}
    issues = [{"a": 1}]
    assert pipeline_mod.review_result_payload(True, issues)["issues"] is issues


def test_review_step_key_bumped_past_poisoned_version():
    """The outer review step_key must no longer be `review:0:` so that
    checkpoints poisoned by the cached service failure no longer match.
    Acceptance plan §6.4: version lives in REVIEW_CHECKPOINT_VERSION with
    v1 initial/rN key naming."""
    src = inspect.getsource(pipeline_mod)
    assert 'review:0:' not in src
    assert 'REVIEW_CHECKPOINT_VERSION' in src
    assert 'review:v{REVIEW_CHECKPOINT_VERSION}:initial:' in src
    assert 'review:v{REVIEW_CHECKPOINT_VERSION}:r{retry_round}:' in src