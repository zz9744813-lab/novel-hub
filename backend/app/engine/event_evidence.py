"""Pure validation for body-grounded canon-event evidence.

Both extraction and finalization use this helper so an event cannot look
commit-eligible during extraction and then be discarded by a different
paragraph-evidence rule at finalization time.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any


def _paragraph_value(paragraph: Any, field: str) -> Any:
    if isinstance(paragraph, Mapping):
        return paragraph.get(field)
    return getattr(paragraph, field, None)


def _paragraph_content(paragraph: Any) -> str:
    return str(_paragraph_value(paragraph, "content") or "")


def _paragraph_hash(paragraph: Any) -> str:
    stored = _paragraph_value(paragraph, "content_hash")
    if stored:
        return str(stored)
    return hashlib.sha256(_paragraph_content(paragraph).encode("utf-8")).hexdigest()


def validate_explicit_event_evidence(
    event: Mapping[str, Any],
    paragraphs_by_key: Mapping[str, Any],
    *,
    allow_without_paragraphs: bool = False,
) -> str | None:
    """Return an error code unless an explicit event is grounded in body text.

    A supplied paragraph key must resolve.  A supplied hash must match the
    resolved paragraph.  Without a key, a non-empty evidence excerpt must be
    an exact substring of a body paragraph; if a hash is supplied it must
    match one of the paragraphs containing that excerpt.

    ``allow_without_paragraphs`` preserves the finalizer's legacy soft path
    for old callers that have no paragraph index.  The state extractor always
    passes ``False`` and therefore never commits an evidence-free event.
    """
    if event.get("certainty") != "explicit":
        return "certainty_not_explicit"

    key = event.get("evidence_paragraph_key") or (
        (event.get("evidence_paragraph_keys") or [None])[0]
    )
    evidence_hash = str(event.get("evidence_hash") or "").strip()

    if key:
        paragraph = paragraphs_by_key.get(str(key))
        if paragraph is None or not _paragraph_content(paragraph).strip():
            return "evidence_key_not_found"
        if evidence_hash and evidence_hash != _paragraph_hash(paragraph):
            return "evidence_hash_mismatch"
        return None

    excerpt = str(
        event.get("evidence") or event.get("evidence_excerpt") or ""
    ).strip()
    if excerpt:
        matches = [
            paragraph
            for paragraph in paragraphs_by_key.values()
            if excerpt in _paragraph_content(paragraph)
        ]
        if matches:
            if evidence_hash and not any(
                evidence_hash == _paragraph_hash(paragraph)
                for paragraph in matches
            ):
                return "evidence_hash_mismatch"
            return None

    if not paragraphs_by_key and allow_without_paragraphs:
        return None
    return "evidence_key_missing"


__all__ = ["validate_explicit_event_evidence"]
