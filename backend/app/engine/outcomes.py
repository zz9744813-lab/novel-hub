"""Typed pipeline outcomes (AI__.md v3.0 §3.3 / B-01)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID


class PipelineOutcome(str, Enum):
    FINALIZED = "finalized"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    RESOURCE_BLOCKED = "resource_blocked"
    PAUSED = "paused"
    NEEDS_HUMAN = "needs_human"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"


@dataclass(frozen=True)
class PipelineResult:
    outcome: PipelineOutcome
    chapter_id: UUID
    chapter_run_id: UUID | None = None
    final_version: int | None = None
    error_code: str | None = None
    detail: dict = field(default_factory=dict)
