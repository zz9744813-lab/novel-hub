"""Operator-approved production model replacements.

These are exact identifiers, not family/name guesses.  They are retired only
because the production gateway has repeatedly failed the release gate for the
exact routes, while ``new-api/glm-5.2`` is present in the live provider catalog.
"""
from __future__ import annotations


PRODUCTION_MODEL_PROVIDER = "new-api"
PRODUCTION_MODEL_ID = "glm-5.2"

RETIRED_PRODUCTION_MODELS = frozenset(
    {
        "deepseek-v4-flash",
        "deepseek-v4-flash-free",
        "stepfun-ai/step-3.7-flash",
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
