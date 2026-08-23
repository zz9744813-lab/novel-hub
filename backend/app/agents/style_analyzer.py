"""v9.2 StyleAnalyzerAgent — semantic style dimensions (spec §43, §44).

Deterministic metrics (sentence length, dialogue ratio, punctuation, lexical
diversity, emotion-word density) are computed by app/style/metrics.py. This
agent only judges the high-level semantic dimensions the Python layer cannot
count — narrative distance, subtext, emotion explicitness, technique patterns.

Contract: output conforms to StyleAnalyzerContract (see app/contracts/agents.py).
"""
from __future__ import annotations

import json
import logging
import uuid

from app.agents.caller import call_agent

logger = logging.getLogger("novelforge.agents.style_analyzer")

SAMPLE_CHARS_PER_SEGMENT = 1500
MAX_SEGMENTS = 16


def wrap_untrusted(text: str) -> str:
    return f"<UNTRUSTED_REFERENCE_TEXT>\n{text}\n</UNTRUSTED_REFERENCE_TEXT>"


async def run_style_analyzer(
    book_id: uuid.UUID,
    *,
    segments: list[str],
    deterministic_metrics: dict | None = None,
    genre_hint: str | None = None,
    **_deprecated,
) -> dict:
    """Run the dedicated style_analyzer agent; returns StyleAnalyzerContract JSON."""
    seg_evidence = [
        {"segment_id": i, "text": wrap_untrusted(seg[:SAMPLE_CHARS_PER_SEGMENT])}
        for i, seg in enumerate(segments[:MAX_SEGMENTS])
    ]

    payload = {
        "task": "produce StyleAnalyzer v2 semantic analysis JSON only",
        "genre_hint": genre_hint or "无",
        "deterministic_metrics": deterministic_metrics or {},
        "segments": seg_evidence,
    }

    try:
        run, publishable, meta = await call_agent(
            book_id=book_id,
            agent_role="style_analyzer",
            user_content=json.dumps(payload, ensure_ascii=False),
            assembly_manifest={
                "entries": [
                    {
                        "type": "untrusted_reference_segments",
                        "chars": sum(len(s) for s in segments),
                    }
                ],
                "excluded_entries": [{"type": "full_reference_original"}],
                "budget": {
                    "max_context": 128000,
                    "reserved_output": 4096,
                    "used": sum(len(s) for s in segments) // 4,
                },
            },
        )
    except Exception as e:
        logger.error("style_analyzer error: %s", e)
        return {"error": str(e), "warnings": ["analyzer_failed"]}

    if isinstance(publishable, dict):
        profile = publishable
    elif isinstance(publishable, str) and publishable:
        from app.gateway.normalizer import normalize_json

        profile = normalize_json(publishable) or {"raw": publishable}
    else:
        return {
            "error": meta.get("error") or "empty",
            "warnings": ["analyzer_failed"],
            "run_id": str(run.id) if run else None,
        }

    profile["_analyzer_run_id"] = str(run.id) if run else None
    return profile
