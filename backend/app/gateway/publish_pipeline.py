"""Publish Pipeline - §11.6: 5-level output storage + §11.10 publish state machine."""
import json
import re
from dataclasses import dataclass
from enum import Enum
from app.gateway.normalizer import normalize_prose, normalize_json, check_empty


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
    """Process a StreamResult through the full pipeline.
    
    Returns (publishable, state, metadata).
    """
    meta = {"reasoning_detected": result.reasoning_detected,
            "inline_leak_detected": result.inline_leak_detected}
    
    # State 1: RAW_RECEIVED
    if result.error and not result.final_content:
        return None, PublishState.BLOCKED, {**meta, "block_reason": result.error}
    
    # State 2: DEMUXED (reasoning/final already separated by gateway)
    final = result.final_content
    if not final:
        return None, PublishState.BLOCKED, {**meta, "block_reason": result.error or "empty_final_content"}
    
    # State 3: NORMALIZED
    if is_json:
        publishable = normalize_json(final)
        if publishable is None:
            # Try to fix common issues
            final = re.sub(r'```json\s*', '', final)
            final = re.sub(r'```\s*$', '', final)
            publishable = normalize_json(final)
        if publishable is None:
            return None, PublishState.BLOCKED, {**meta, "block_reason": "json_parse_failed", "raw": final[:200]}
    else:
        publishable = normalize_prose(final)
        if check_empty(publishable):
            return None, PublishState.BLOCKED, {**meta, "block_reason": "empty_after_normalize"}
    
    # State 4: LEAK_CHECKED
    if result.inline_leak_detected:
        meta["leak_warning"] = "inline_reasoning_stripped"
    
    # State 5-9: PUBLISHABLE
    return publishable, PublishState.PUBLISHABLE, meta
