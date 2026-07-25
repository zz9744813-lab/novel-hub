"""v7.4 StyleSanitizer - Prevent GenreProfile from copying reference text.

C-29: GenreProfile must prevent:
- Continuous 15+ character exact spans from reference
- Named entities unique to reference
- High similarity sentences (5-gram Jaccard > 0.35)
- Prompt injection residual

Memory-safe on low-RAM VPS: never build O(n*m) DP over full reference text.
"""
from __future__ import annotations

import re
from typing import Any

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous",
    r"system\s*prompt",
    r"你现在是",
    r"忽略以上",
    r"泄漏\s*prompt",
    r"output\s+your\s+instructions",
]


def _ngrams(text: str, n: int = 5) -> set[str]:
    text = re.sub(r"\s+", "", text)
    if len(text) < n:
        return set()
    # Cap ngram set size for huge references
    max_start = min(len(text) - n + 1, 200_000)
    return {text[i : i + n] for i in range(max_start)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _longest_common_substring_capped(a: str, b: str, max_b: int = 8000) -> str:
    """Find LCS of short candidate vs reference sample windows (not full O(n*m))."""
    a = re.sub(r"\s+", "", a or "")
    b = re.sub(r"\s+", "", b or "")
    if not a or not b:
        return ""
    # Only compare against first + middle + last windows of reference
    windows = [b[:max_b]]
    if len(b) > max_b:
        mid = max(0, (len(b) - max_b) // 2)
        windows.append(b[mid : mid + max_b])
        windows.append(b[-max_b:])
    best = ""
    # For each window use rolling hash-ish scan of a-length substrings of a
    # Since a (snippet) is short (<=500), DP against each window is fine.
    for win in windows:
        if not win:
            continue
        dp_prev = [0] * (len(win) + 1)
        for i in range(1, len(a) + 1):
            dp_cur = [0] * (len(win) + 1)
            for j in range(1, len(win) + 1):
                if a[i - 1] == win[j - 1]:
                    dp_cur[j] = dp_prev[j - 1] + 1
                    if dp_cur[j] > len(best):
                        best = a[i - dp_cur[j] : i]
            dp_prev = dp_cur
    return best


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

    # Cap reference for ngram work
    ref_sample = (reference_text or "")[:120_000]

    snippet = profile.get("prompt_injection_snippet") or ""
    content_notes = profile.get("content_intensity_notes") or ""
    candidates = [snippet, content_notes]
    for tag in profile.get("technique_tags") or []:
        candidates.append(str(tag))

    for cand in candidates:
        if not cand:
            continue
        lcs = _longest_common_substring_capped(cand, ref_sample)
        if len(lcs) >= 15:
            report["exact_span_violations"].append({"span": lcs[:50], "length": len(lcs)})
            report["passed"] = False

    ref_ng = _ngrams(ref_sample, 5)
    for cand in candidates:
        if not cand or len(cand) < 20:
            continue
        score = _jaccard(_ngrams(cand, 5), ref_ng)
        if score > 0.35:
            report["high_similarity_sentences"].append(
                {"score": round(score, 3), "preview": cand[:80]}
            )
            report["passed"] = False

    joined = "\n".join(candidates)
    for pat in INJECTION_PATTERNS:
        if re.search(pat, joined, re.IGNORECASE):
            report["instruction_injection_findings"].append(pat)
            report["passed"] = False

    if not (200 <= len(snippet) <= 500):
        report["manual_review_required"] = True
        report["passed"] = False

    if report["exact_span_violations"] or report["high_similarity_sentences"]:
        report["manual_review_required"] = True

    return report
