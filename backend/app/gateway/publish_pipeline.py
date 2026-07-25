"""Publish Pipeline - §11.6: 5-level output storage + §11.10 publish state machine.
§11.8: AILeakGuard integrated at LEAK_CHECKED state.
"""
from __future__ import annotations

import re
from enum import Enum

from app.gateway.normalizer import normalize_prose, normalize_json, check_empty
from app.gateway.leak_guard import check_leak, check_leak_async


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


def _normalize(final: str, is_json: bool, meta: dict):
    if is_json:
        publishable = normalize_json(final)
        if publishable is None:
            fixed = re.sub(r"```json\s*", "", final)
            fixed = re.sub(r"```\s*$", "", fixed)
            publishable = normalize_json(fixed)
        if publishable is None:
            return None, PublishState.BLOCKED, {
                **meta,
                "block_reason": "json_parse_failed",
                "raw": final[:200],
            }
        return publishable, PublishState.NORMALIZED, meta

    publishable = normalize_prose(final)
    if check_empty(publishable):
        return None, PublishState.BLOCKED, {
            **meta,
            "block_reason": "empty_after_normalize",
        }
    return publishable, PublishState.NORMALIZED, meta


def full_pipeline(result, is_json: bool = False):
    """Sync pipeline: Layer0+1 only. Prefer full_pipeline_async for prose.

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

    if result.error and not result.final_content:
        return None, PublishState.BLOCKED, {**meta, "block_reason": result.error}

    final = result.final_content
    if not final:
        return None, PublishState.BLOCKED, {
            **meta,
            "block_reason": result.error or "empty_final_content",
        }

    publishable, state, meta = _normalize(final, is_json, meta)
    if state == PublishState.BLOCKED:
        return publishable, state, meta

    if not is_json:
        # publishable is always str after normalize_prose
        text_body = publishable if isinstance(publishable, str) else str(publishable)
        leak_result = check_leak(text_body, getattr(result, "reasoning_text", None))
        meta["leak_findings_count"] = len(leak_result.findings)
        meta["contamination_ratio"] = leak_result.contamination_ratio
        meta["layer1_candidates"] = len(leak_result.layer1_candidates)

        if leak_result.block_candidate:
            return None, PublishState.BLOCKED, {
                **meta,
                "block_reason": leak_result.block_reason or "leak_detected",
                "leak_findings": leak_result.findings[:10],
                "contamination_ratio": leak_result.contamination_ratio,
            }

        if leak_result.findings or leak_result.layer1_candidates:
            meta["leak_warnings"] = [
                (f.get("evidence_span") or f.get("span") or "")[:80]
                for f in (leak_result.findings or leak_result.layer1_candidates)[:5]
            ]

    return publishable, PublishState.PUBLISHABLE, meta


async def full_pipeline_async(
    result,
    is_json: bool = False,
    agent_role: str = "draft_writer",
    book_id=None,
):
    """Async pipeline with Layer-2 AILeakJudge for prose content."""
    meta = {
        "reasoning_detected": result.reasoning_detected,
        "inline_leak_detected": result.inline_leak_detected,
        "provider_used": getattr(result, "provider_used", "primary"),
        "attempt": getattr(result, "attempt", 0),
    }

    if result.error and not result.final_content:
        return None, PublishState.BLOCKED, {**meta, "block_reason": result.error}

    final = result.final_content
    if not final:
        return None, PublishState.BLOCKED, {
            **meta,
            "block_reason": result.error or "empty_final_content",
        }

    publishable, state, meta = _normalize(final, is_json, meta)
    if state == PublishState.BLOCKED:
        return publishable, state, meta

    if not is_json:
        text_body = publishable if isinstance(publishable, str) else str(publishable)
        leak_result = await check_leak_async(
            text_body,
            reasoning=getattr(result, "reasoning_text", None),
            agent_role=agent_role,
            book_id=book_id,
        )
        meta["leak_findings_count"] = len(leak_result.findings)
        meta["contamination_ratio"] = leak_result.contamination_ratio
        meta["layer1_candidates"] = len(leak_result.layer1_candidates)
        meta["layer2_judgments"] = len(leak_result.layer2_judgments)

        if leak_result.block_candidate:
            return None, PublishState.BLOCKED, {
                **meta,
                "block_reason": leak_result.block_reason or "leak_detected",
                "leak_findings": leak_result.findings[:10],
                "contamination_ratio": leak_result.contamination_ratio,
            }

        if leak_result.findings or leak_result.layer1_candidates:
            meta["leak_warnings"] = [
                (f.get("evidence_span") or f.get("span") or "")[:80]
                for f in (leak_result.findings or leak_result.layer1_candidates)[:5]
            ]

    return publishable, PublishState.PUBLISHABLE, meta
