"""Compatibility facade for the unified document parser layer."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.document_parsers import parse_document


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _legacy_block(block: dict[str, Any]) -> dict[str, Any]:
    """Keep the v8.0 JSON shape while exposing the richer parser metadata."""
    return {
        "block_id": block["block_id"],
        "id": block["block_id"],
        "type": block["type"],
        "level": block.get("level"),
        "text": block.get("text") or "",
        "ordinal": block["ordinal"],
        "section_path": block.get("section_path") or [],
        "source_locator": block.get("source_locator") or {},
        "metadata": block.get("metadata") or {},
        "page": (block.get("source_locator") or {}).get("page", 1),
    }


def extract_file(path: Path, original_filename: str, document_id: str) -> dict[str, Any]:
    """Parse a supported document without truncating or fabricating fallback text."""
    blocks = [_legacy_block(block.to_dict()) for block in parse_document(path, document_id)]
    raw = path.read_bytes()
    requires_ocr_pages = sorted(
        {
            int((block.get("source_locator") or {}).get("page"))
            for block in blocks
            if (block.get("metadata") or {}).get("requires_ocr")
            and (block.get("source_locator") or {}).get("page") is not None
        }
    )
    return {
        "document_id": document_id,
        "blocks": blocks,
        "meta": {
            "original_filename": original_filename,
            "char_count": sum(len(block.get("text") or "") for block in blocks),
            "block_count": len(blocks),
            "sha256": sha256_bytes(raw),
            "parser_version": "v8.1",
            "requires_ocr": bool(requires_ocr_pages),
            "requires_ocr_pages": requires_ocr_pages,
        },
    }


CHATTER_PATTERNS = [
    r"如果您?觉得满意",
    r"我们可以继续",
    r"^收到",
    r"没问题，我们保持这个节奏",
    r"这份细纲是否符合",
    r"准备好开始了吗",
    r"是否进入下一卷",
    r"如果满意可以开始",
]


def candidate_sanitize(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rule-only candidates for ImportSanitizerAgent."""
    import re

    out = []
    for block in blocks:
        text = block.get("text") or ""
        classification = "source_content"
        action = "keep"
        reason = "default_keep"
        confidence = 0.5
        for pattern in CHATTER_PATTERNS:
            if re.search(pattern, text):
                classification = "assistant_chatter"
                action = "review"
                reason = f"matched_candidate:{pattern}"
                confidence = 0.7
                break
        out.append(
            {
                "block_id": block["block_id"],
                "classification": classification,
                "action": action,
                "confidence": confidence,
                "reason": reason,
            }
        )
    return out
