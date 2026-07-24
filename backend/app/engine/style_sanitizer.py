"""v7.4 StyleSanitizer - Prevent GenreProfile from copying reference text.

C-29: GenreProfile must prevent:
- Continuous 15+ character exact spans from reference
- Named entities unique to reference
- High similarity sentences (5-gram Jaccard > 0.35)
- Prompt injection residual
"""
from __future__ import annotations

import re
from typing import Any


def _ngrams(text: str, n: int = 5) -> set[str]:
    text = re.sub(r"\s+", "", text)
    if len(text) < n:
        return set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _longest_common_substring(a: str, b: str) -> str:
    a = re.sub(r"\s+", "", a)
    b = re.sub(r"\s+", "", b)
    if not a or not b:
        return ""
    best = ""
    # O(n*m) but limited by short snippets
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
                if dp[i][j] > len(best):
                    best = a[i - dp[i][j] : i]
    return best


INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous",
    r"system\s*prompt",
    r"你现在是",
    r"忽略以上",
    r"泄漏\s*prompt",
    r"output\s+your\s+instructions",
]


def sanitize_genre_profile(
    profile: dict[str, Any],
    reference_text: str,
) -> dict[str, Any]:
    """Run StyleSanitizer checks. Returns sanitizer_report JSON."""
    report: dict[str, Any] = {
        "passed": True,
        "exact_span_violations": [],
        "named_entity_violations": [],
        "high_similarity_sentences": [],
        "instruction_injection_findings": [],
        "manual_review_required": False,
    }

    snippet = profile.get("prompt_injection_snippet") or ""
    content_notes = profile.get("content_intensity_notes") or ""
    candidates = [snippet, content_notes]
    for tag in profile.get("technique_tags") or []:
        candidates.append(str(tag))

    # Exact span >= 15
    for cand in candidates:
        if not cand:
            continue
        lcs = _longest_common_substring(cand, reference_text)
        if len(lcs) >= 15:
            report["exact_span_violations"].append({"span": lcs[:50], "length": len(lcs)})
            report["passed"] = False

    # 5-gram Jaccard
    ref_ng = _ngrams(reference_text, 5)
    for cand in candidates:
        if not cand or len(cand) < 20:
            continue
        score = _jaccard(_ngrams(cand, 5), ref_ng)
        if score > 0.35:
            report["high_similarity_sentences"].append({"score": round(score, 3), "preview": cand[:80]})
            report["passed"] = False

    # Injection residual in profile fields
    joined = "\n".join(candidates)
    for pat in INJECTION_PATTERNS:
        if re.search(pat, joined, re.IGNORECASE):
            report["instruction_injection_findings"].append(pat)
            report["passed"] = False

    # Snippet length
    if not (200 <= len(snippet) <= 500):
        report["manual_review_required"] = True
        report["passed"] = False

    if report["exact_span_violations"] or report["high_similarity_sentences"]:
        report["manual_review_required"] = True

    return report
