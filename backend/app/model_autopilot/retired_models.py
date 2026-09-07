"""Operator-approved production model replacements.

These are exact identifiers, not family/name guesses.  They are retired only
because the production gateway has repeatedly failed the release gate for the
exact routes. On 2026-09-05 GLM-5.2 returned model_not_found; the replacement
GLM-5.3-Flash answered an exact text handshake. Ability remains release-gated.
"""
from __future__ import annotations


PRODUCTION_MODEL_PROVIDER = "new-api"
PRODUCTION_MODEL_ID = "glm-5.3-flash"

RETIRED_PRODUCTION_MODELS = frozenset(
    {
        "deepseek-v4-flash",
        "deepseek-v4-flash-free",
        "stepfun-ai/step-3.7-flash",
        "glm-5.2",
        "z-ai/glm-5.2",
    }
)


def normalize_production_model(model_id: str | None) -> str | None:
    """Map only the explicitly retired exact ids to the approved replacement."""

    if model_id in RETIRED_PRODUCTION_MODELS:
        return PRODUCTION_MODEL_ID
    return model_id


def is_retired_production_model(model_id: str | None) -> bool:
    return bool(model_id and model_id in RETIRED_PRODUCTION_MODELS)


__all__ = [
    "PRODUCTION_MODEL_ID",
    "PRODUCTION_MODEL_PROVIDER",
    "RETIRED_PRODUCTION_MODELS",
    "is_retired_production_model",
    "normalize_production_model",
]
