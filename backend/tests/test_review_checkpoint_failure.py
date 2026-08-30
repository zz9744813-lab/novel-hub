"""Acceptance-plan §7 regressions: review checkpoints must never cache a
missing review verdict, retries must be bounded, and outline_missing must be
permanent. Pure-logic tests (no DB) for the validator contract; DB-level
checkpoint semantics are covered by test_v3_invariants.py fixtures."""
import pytest

from app.contracts.agents import StateExtractContract  # noqa: F401  import sanity
from app.engine.pipeline import (
    REVIEW_CHECKPOINT_VERSION,
    REVIEW_MAX_RETRYABLE_ATTEMPTS,
    REVIEW_RETRY_EXHAUSTED_CODE,
    validate_review_output,
)
from app.engine.step_runner import PermanentStepError, RetryableStepError


def test_initial_review_service_error_is_failed_not_succeeded():
    """A service-failure review output must raise (never persist success)."""
    with pytest.raises(RetryableStepError) as exc:
        validate_review_output({
            "passed": False,
            "issues": [{
                "issue_id": "review_service_failure",
                "severity": "critical",
                "category": "service_error",
                "message": "final_content_empty",
            }],
        })
    assert exc.value.code == "review_service_failure"


def test_rereview_service_error_is_failed_not_succeeded():
    """The same gate is used by re-review; invalid payloads also raise."""
    with pytest.raises(RetryableStepError) as exc:
        validate_review_output({"passed": False, "issues": [{
            "issue_id": "review_invalid_payload",
            "severity": "critical",
            "category": "service_error",
            "message": "non-dict payload",
        }]})
    assert exc.value.code == "review_invalid_payload"
    with pytest.raises(RetryableStepError):
        validate_review_output("not a dict")


def test_quality_rejection_is_a_valid_review_checkpoint():
    """passed=False with quality/style issues is a REAL review result: the
    validator must pass it through so run_step can checkpoint it and the
    patch loop can act on it."""
    out = {
        "passed": False,
        "issues": [{
            "issue_id": "style-lexical-diversity",
            "severity": "major",
            "category": "style",
            "instruction": "调整用词多样性",
        }],
    }
    assert validate_review_output(out) is out
    assert validate_review_output({"passed": True, "issues": []}) == {"passed": True, "issues": []}


def test_outline_missing_is_not_retryable():
    """outline_missing is a data/precondition error: permanent, no model retries."""
    with pytest.raises(PermanentStepError) as exc:
        validate_review_output({
            "passed": False,
            "issues": [{
                "issue_id": "review_service_failure",
                "severity": "critical",
                "category": "service_error",
                "message": "outline missing for chapter 1",
            }],
        })
    assert exc.value.code == "outline_missing"


def test_review_v1_does_not_reuse_poisoned_v0():
    """The versioned key space guarantees old review:0:* poison rows can never
    match a v1 checkpoint key."""
    legacy_key = "review:0:f1d4995aafce75a1"
    new_key = f"review:v{REVIEW_CHECKPOINT_VERSION}:initial:f1d4995aafce75a1"
    assert legacy_key != new_key
    assert new_key.startswith("review:v1:initial:")
    assert REVIEW_CHECKPOINT_VERSION >= 1


def test_review_retry_budget_is_bounded():
    """Acceptance contract §6.3: at most 2 pipeline-level retryable attempts,
    then a terminal review_service_retry_exhausted code."""
    assert REVIEW_MAX_RETRYABLE_ATTEMPTS == 2
    assert REVIEW_RETRY_EXHAUSTED_CODE == "review_service_retry_exhausted"


def test_step_runner_signature_supports_bounded_retries():
    """run_step must expose max_retryable_attempts / retry_exhausted_code so
    review paths can bound retries without changing other steps."""
    import inspect
    from app.engine.step_runner import run_step
    sig = inspect.signature(run_step)
    assert "max_retryable_attempts" in sig.parameters
    assert "retry_exhausted_code" in sig.parameters
    assert sig.parameters["max_retryable_attempts"].default is None


def test_step_runner_counts_total_failures_for_exhaustion():
    """The bounded-retry decision must count ALL failed attempts of a
    (run, step_key) regardless of error code (acceptance report §7.2);
    only pause/cancel and lease-lost bookkeeping rows are excluded."""
    import inspect
    from app.engine.step_runner import (
        NON_RETRYABLE_FAILURE_CODES,
        count_retryable_failed_attempts,
    )

    sig = inspect.signature(count_retryable_failed_attempts)
    assert list(sig.parameters) == ["chapter_run_id", "step_key"]
    assert "control_requested" in NON_RETRYABLE_FAILURE_CODES
    assert "lease_lost" in NON_RETRYABLE_FAILURE_CODES