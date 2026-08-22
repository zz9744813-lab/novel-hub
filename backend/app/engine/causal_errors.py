"""v9.1 CCNE error taxonomy (spec §3.3).

Fail-closed policy: CCNE core failures must never silently degrade to
"draft without contracts". Soft information may degrade; hard constraints
must block or retry.

Soft (may degrade):  soft appraisal, expression tendency, non-key soft edge,
                    non-locked missing Core anchor.
Fail-closed:        hard precondition, hard effect, knowledge boundary,
                    pivotal intention, state path, contract schema,
                    deterministic engine runtime error.
"""
from __future__ import annotations


class CausalCompileError(Exception):
    """Base class for CCNE compile failures that must NOT silently degrade."""


class CausalHardBlockError(CausalCompileError):
    """Hard constraint violated — requires human resolution (fail-closed)."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail or ''}")


class CausalRuntimeError(CausalCompileError):
    """Deterministic engine runtime error — retryable pipeline failure."""
