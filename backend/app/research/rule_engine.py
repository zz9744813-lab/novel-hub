"""Declarative rule evaluation + selector candidate ranking (spec §12, §13).

The full Rule v2 JSON schema (§12) is forward-looking; this module implements
the immediately useful part — ranking candidate selectors by extraction quality —
so probes can recommend better selectors when the configured one stops matching.
"""
from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

# Minimum content floor before a selector is considered a viable candidate.
MIN_CANDIDATE_CHARS = 50


def _extract_text(soup: BeautifulSoup, selector: str) -> str:
    try:
        els = soup.select(selector)
    except Exception:
        return ""
    parts = [el.get_text(separator="\n", strip=True) for el in els if el.get_text(strip=True)]
    return "\n\n".join(parts).strip()


def quality_score(text: str) -> float:
    """Heuristic content-quality score in [0, 1] (spec §13 quality scoring)."""
    if not text:
        return 0.0
    n = len(text)
    score = min(1.0, n / 2000.0) * 0.5  # length
    # sentence punctuation density signals prose (not nav boilerplate)
    punct = len(re.findall(r"[，。！？；：、,.!?;:]", text))
    score += min(0.3, punct / max(n, 1) * 30)
    # paragraph structure
    paragraphs = [p for p in text.split("\n") if len(p.strip()) >= 8]
    score += min(0.2, len(paragraphs) / 10)
    return round(min(1.0, score), 3)


def rank_content_candidates(
    soup: BeautifulSoup,
    selectors: list[str],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Try each selector, score extracted text, return ranked candidates."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sel in selectors:
        if not sel or sel in seen:
            continue
        seen.add(sel)
        text = _extract_text(soup, sel)
        if len(text) >= MIN_CANDIDATE_CHARS:
            results.append(
                {"selector": sel, "chars": len(text), "score": quality_score(text)}
            )
    results.sort(key=lambda r: (-r["score"], -r["chars"]))
    return results[:limit]
