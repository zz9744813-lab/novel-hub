"""Lightweight Chinese pre-tokenizer for PostgreSQL simple tsvector.

Postgres 'simple' config treats CJK as continuous tokens without spaces.
We insert spaces between CJK runs / words using a pragmatic segmenter:
1) Keep Latin/digits as tokens
2) Split CJK into 2-grams + unigrams for coverage
3) Preserve explicit punctuation as separators

Not a full NLP segmenter — good enough for recall on low-resource VPS.
"""
from __future__ import annotations

import re

_LATIN = re.compile(r"[A-Za-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
_SPACE = re.compile(r"\s+")


def tokenize_for_search(text: str) -> str:
    """Return space-separated tokens suitable for to_tsvector('simple', ...)."""
    if not text:
        return ""
    text = _SPACE.sub(" ", text).strip()
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        # Latin / digit run
        m = _LATIN.match(text, i)
        if m:
            tokens.append(m.group(0).lower())
            i = m.end()
            continue
        # CJK run → bigrams + unigrams
        m = _CJK.match(text, i)
        if m:
            run = m.group(0)
            for j in range(len(run)):
                tokens.append(run[j])
                if j + 1 < len(run):
                    tokens.append(run[j : j + 2])
            i = m.end()
            continue
        # skip other punctuation
        i += 1
    # de-dupe while preserving order for stability
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return " ".join(out)
