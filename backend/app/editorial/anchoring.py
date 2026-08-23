"""Annotation anchoring: composite anchor + relocation across versions.

Spec §16: offsets alone drift when the text changes. Every annotation
stores paragraph_key + quoted_text + quote_hash + context_before/after +
context_hash. Relocation ladder:

    paragraph exact → quote exact → context fuzzy → unresolved
"""
from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass

PARA_SPLIT = "\n\n"
CONTEXT_CHARS = 60
FUZZY_THRESHOLD = 0.60


def split_paragraphs(content: str) -> list[str]:
    return [p for p in content.split(PARA_SPLIT)]


def sha256_short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class AnchorContext:
    """Server-computed anchor payload for one annotation."""

    context_before: str
    context_after: str
    quote_hash: str
    context_hash: str


def build_anchor(paragraphs: list[str], paragraph_key: int, quoted_text: str) -> AnchorContext:
    """Compute quote/context hashes and surrounding context for a quote."""
    idx = int(paragraph_key)
    para = paragraphs[idx] if 0 <= idx < len(paragraphs) else ""
    pos = para.find(quoted_text)
    if pos < 0:
        raise ValueError("QUOTE_NOT_IN_PARAGRAPH")
    before_full = paragraphs[max(0, idx - 1)][-CONTEXT_CHARS:] if idx > 0 else ""
    before_para = para[:pos][-CONTEXT_CHARS:]
    context_before = (before_full + before_para)[-CONTEXT_CHARS:]
    after_start = pos + len(quoted_text)
    after_para = para[after_start:][:CONTEXT_CHARS]
    after_full = paragraphs[idx + 1][:CONTEXT_CHARS] if idx + 1 < len(paragraphs) else ""
    context_after = (after_para + after_full)[:CONTEXT_CHARS]
    return AnchorContext(
        context_before=context_before,
        context_after=context_after,
        quote_hash=sha256_short(quoted_text),
        context_hash=sha256_short(context_before + quoted_text + context_after),
    )


@dataclass
class RelocationResult:
    paragraph_key: int | None
    start_offset: int
    end_offset: int
    resolution_status: str  # open (still valid) | moved | unresolved
    method: str  # paragraph_exact | quote_exact | context_fuzzy | unresolved


def _find_in_paragraph(para: str, quoted_text: str) -> tuple[int, int] | None:
    pos = para.find(quoted_text)
    if pos < 0:
        return None
    return pos, pos + len(quoted_text)


def relocate(paragraphs: list[str], annotation: dict) -> RelocationResult:
    """Re-locate one annotation against a new paragraph list (spec §16 ladder)."""
    quoted = annotation.get("quoted_text") or ""
    if not quoted:
        return RelocationResult(None, 0, 0, "unresolved", "unresolved")

    # 1) paragraph exact
    old_key = annotation.get("paragraph_key")
    if old_key is not None:
        try:
            idx = int(old_key)
        except (TypeError, ValueError):
            idx = -1
        if 0 <= idx < len(paragraphs):
            span = _find_in_paragraph(paragraphs[idx], quoted)
            if span:
                return RelocationResult(idx, span[0], span[1], "open", "paragraph_exact")

    # 2) quote exact
    for i, para in enumerate(paragraphs):
        span = _find_in_paragraph(para, quoted)
        if span:
            return RelocationResult(i, span[0], span[1], "moved", "quote_exact")

    # 3) context fuzzy
    ctx_before = annotation.get("context_before") or ""
    ctx_after = annotation.get("context_after") or ""
    target = f"{ctx_before}{quoted}{ctx_after}"
    best_ratio, best_idx = 0.0, -1
    for i, para in enumerate(paragraphs):
        window = para[max(0, len(para) // 4 - 40): len(para) // 4 + len(target) + 80]
        ratio = max(
            difflib.SequenceMatcher(None, target, para).ratio(),
            difflib.SequenceMatcher(None, target, window).ratio(),
        )
        if ratio > best_ratio:
            best_ratio, best_idx = ratio, i
    if best_ratio >= FUZZY_THRESHOLD:
        return RelocationResult(best_idx, 0, 0, "moved", "context_fuzzy")

    return RelocationResult(None, 0, 0, "unresolved", "unresolved")


def diff_paragraphs(old: list[str], new: list[str]) -> dict:
    """Coarse paragraph-level diff for the review UI (§97).

    Returns per-new-paragraph status: kept | changed | added, plus the set
    of removed old indices — enough for humans to focus re-review.
    """
    matcher = difflib.SequenceMatcher(None, old, new)
    kept: list[int] = []
    changed: list[int] = []
    added: list[int] = []
    removed: list[int] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            kept.extend(range(j1, j2))
        elif tag == "replace":
            changed.extend(range(j1, j2))
            removed.extend(range(i1, i2))
        elif tag == "insert":
            added.extend(range(j1, j2))
        elif tag == "delete":
            removed.extend(range(i1, i2))
    return {"kept": kept, "changed": changed, "added": added, "removed": removed}
