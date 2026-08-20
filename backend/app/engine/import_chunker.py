"""Lossless, heading-aware map chunks for long import documents."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from typing import Any


@dataclass(frozen=True)
class ImportChunk:
    chunk_index: int
    section_path: list[str]
    block_ids: list[str]
    text: str
    char_count: int
    token_estimate: int
    content_hash: str
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(block: dict[str, Any]) -> str:
    block_id = block.get("block_id") or block.get("id") or ""
    value = (block.get("text") or "").strip()
    return f"[{block_id}] {value}" if value else f"[{block_id}]"


def _split_long_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = max(text.rfind("。", start, end), text.rfind("\n", start, end), text.rfind(" ", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        pieces.append(text[start:end])
        start = end
    return pieces


def chunk_document_blocks(
    blocks: list[dict[str, Any]],
    *,
    target_chars: int = 8_000,
    max_chars: int = 12_000,
    overlap_chars: int = 400,
) -> list[ImportChunk]:
    """Create ordered chunks while guaranteeing every source block is represented."""
    if target_chars <= overlap_chars or max_chars < target_chars:
        raise ValueError("target_chars/max_chars/overlap_chars are inconsistent")
    chunks: list[ImportChunk] = []
    current_text = ""
    current_ids: list[str] = []
    current_section: list[str] = []
    block_fragments: list[tuple[str, str, list[str]]] = []
    for block in blocks:
        block_id = str(block.get("block_id") or block.get("id") or f"block-{len(block_fragments):06d}")
        value = _text({**block, "block_id": block_id})
        fragments = _split_long_text(value, max_chars - 100)
        for fragment in fragments:
            block_fragments.append((block_id, fragment, list(block.get("section_path") or [])))

    def emit(text: str, ids: list[str], section: list[str]):
        if not text.strip():
            return
        index = len(chunks)
        chunks.append(
            ImportChunk(
                chunk_index=index,
                section_path=section,
                block_ids=list(dict.fromkeys(ids)),
                text=text,
                char_count=len(text),
                token_estimate=max(1, len(text) // 4),
                content_hash=sha256(text.encode("utf-8")).hexdigest(),
            )
        )

    for block_id, fragment, section in block_fragments:
        heading_boundary = bool(section != current_section and current_text.strip())
        would_overflow = len(current_text) + len(fragment) + (1 if current_text else 0) > target_chars
        if current_text and (heading_boundary or would_overflow):
            emit(current_text, current_ids, current_section)
            overlap = current_text[-overlap_chars:]
            current_text = overlap
            current_ids = [current_ids[-1]] if current_ids else []
        if current_text:
            current_text += "\n"
        current_text += fragment
        current_ids.append(block_id)
        current_section = section
        if len(current_text) >= max_chars:
            emit(current_text[:max_chars], current_ids, current_section)
            current_text = current_text[max_chars - overlap_chars :]
            current_ids = [block_id]
    emit(current_text, current_ids, current_section)
    return chunks


def coverage_report(blocks: list[dict[str, Any]], chunks: list[ImportChunk], excluded_block_ids: set[str] | None = None) -> dict[str, Any]:
    excluded = excluded_block_ids or set()
    all_ids = {str(b.get("block_id") or b.get("id")) for b in blocks}
    processed = {block_id for chunk in chunks for block_id in chunk.block_ids}
    processed -= excluded
    unprocessed = all_ids - processed - excluded
    total = len(all_ids)
    return {
        "total_blocks": total,
        "processed_blocks": len(processed),
        "excluded_blocks": len(excluded & all_ids),
        "unprocessed_blocks": len(unprocessed),
        "unprocessed_block_ids": sorted(unprocessed),
        "coverage_pct": round((len(processed) / total * 100) if total else 100.0, 2),
    }
