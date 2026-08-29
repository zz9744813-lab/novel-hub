"""P1 regression: review service errors must not be cached as succeeded
checkpoints (poisoned checkpoint), and the review step key version must have
been bumped past the poisoned `review:0:` era."""
from app.gateway.model_gateway import _generation_controls  # noqa: F401  (import sanity)
import inspect

import app.engine.pipeline as pipeline_mod


def _source_of_do_review():
    src = inspect.getsource(pipeline_mod)
    # Extract the outer _do_review function source
    start = src.index("async def _do_review(_payload):")
    end = src.index("try:", start)
    return src[start:end]


def test_do_review_raises_on_service_error():
    """service_error issues must raise RetryableStepError, never be returned
    as a normal payload (which run_step would cache as succeeded)."""
    body = _source_of_do_review()
    assert "RetryableStepError" in body
    assert 'raise RetryableStepError("review_service_error"' in body


def test_do_review_returns_payload_only_for_real_reviews():
    body = _source_of_do_review()
    # The normal return only happens after the service-error guard.
    guard_pos = body.index('raise RetryableStepError("review_service_error"')
    ret_pos = body.index('return {"passed": bool(p), "issues": iss or []}')
    assert guard_pos < ret_pos


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