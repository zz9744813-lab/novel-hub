"""Publish Pipeline - §11.6: 5-level output storage + §11.10 publish state machine.
§11.8: AILeakGuard integrated at LEAK_CHECKED state.
"""
import json
import re
from dataclasses import dataclass
from enum import Enum
from app.gateway.normalizer import normalize_prose, normalize_json, check_empty
from app.gateway.leak_guard import check_leak, LeakResult


class PublishState(str, Enum):
    RAW_RECEIVED = "raw_received"
    DEMUXED = "demuxed"
    NORMALIZED = "normalized"
    LEAK_CHECKED = "leak_checked"
    REVIEWED = "reviewed"
    PATCHED = "patched"
    CONTINUITY_PASSED = "continuity_passed"
    PUBLISHABLE = "publishable"
    FINALIZED = "finalized"
    BLOCKED = "blocked"


def full_pipeline(result, is_json: bool = False):
    """Process a StreamResult through the full publish pipeline.

    States per §11.10:
    RAW_RECEIVED -> DEMUXED -> NORMALIZED -> LEAK_CHECKED -> PUBLISHABLE
    Any failure at any state -> BLOCKED
    """
    meta = {
        "reasoning_detected": result.reasoning_detected,
        "inline_leak_detected": result.inline_leak_detected,
        "provider_used": getattr(result, "provider_used", "primary"),
        "attempt": getattr(result, "attempt", 0),
    }

    # State 1: RAW_RECEIVED - check for provider errors
    if result.error and not result.final_content:
        return None, PublishState.BLOCKED, {**meta, "block_reason": result.error}

    # State 2: DEMUXED - reasoning/final already separated by gateway
    final = result.final_content
    if not final:
        return None, PublishState.BLOCKED, {
            **meta,
            "block_reason": result.error or "empty_final_content",
        }

    # State 3: NORMALIZED
    if is_json:
        publishable = normalize_json(final)
        if publishable is None:
            # Try to fix common issues
            fixed = re.sub(r'```json\s*', '', final)
            fixed = re.sub(r'```\s*$', '', fixed)
            publishable = normalize_json(fixed)
        if publishable is None:
            return None, PublishState.BLOCKED, {
                **meta,
                "block_reason": "json_parse_failed",
                "raw": final[:200],
            }
    else:
        publishable = normalize_prose(final)
        if check_empty(publishable):
            return None, PublishState.BLOCKED, {
                **meta,
                "block_reason": "empty_after_normalize",
            }

    # State 4: LEAK_CHECKED - §11.8 AILeakGuard three-layer detection
    if not is_json:
        leak_result = check_leak(publishable)
        meta["leak_findings_count"] = len(leak_result.findings)
        meta["contamination_ratio"] = leak_result.contamination_ratio

        if leak_result.block_candidate:
            # §11.9: Block candidate if contamination > 10% or >= 3 inline leaks
            return None, PublishState.BLOCKED, {
                **meta,
                "block_reason": "leak_detected",
                "leak_findings": leak_result.findings[:10],
                "contamination_ratio": leak_result.contamination_ratio,
            }

        if leak_result.findings:
            # Non-blocking findings: attach as warnings
            meta["leak_warnings"] = [
                f["evidence_span"][:80] for f in leak_result.findings[:5]
            ]

    # State 5-9: PUBLISHABLE
    return publishable, PublishState.PUBLISHABLE, meta
