"""Plain text and Markdown parser preserving headings and order."""
from __future__ import annotations

import re
from pathlib import Path

from .base import DocumentBlock

_CN_HEADING = re.compile(r"^第[一二三四五六七八九十百千万0-9]+[卷章部篇].*$", re.I)
_EN_HEADING = re.compile(r"^(?:chapter|volume|part|ch\.?)[ .:_-]*\d+.*$", re.I)


def parse_text(text: str, document_id: str, *, source_name: str = "") -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    section_path: list[str] = []
    ordinal = 0
    for line_no, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        value = raw.strip()
        if not value:
            continue
        ordinal += 1
        block_type = "paragraph"
        level: int | None = None
        if value.startswith("#"):
            match = re.match(r"^(#{1,6})\s*(.*)$", value)
            assert match
            level = len(match.group(1))
            value = match.group(2).strip()
            block_type = "heading"
        elif _CN_HEADING.match(value) or _EN_HEADING.match(value):
            block_type = "heading"
            level = 2
        elif re.match(r"^(?:[-*+•]|\d+[.)])\s+", value):
            block_type = "list"
            value = re.sub(r"^(?:[-*+•]|\d+[.)])\s+", "", value)
        elif value.startswith(">"):
            block_type = "quote"
            value = value[1:].strip()
        elif value.startswith("```"):
            block_type = "code"
        if block_type == "heading" and level is not None:
            section_path = section_path[: level - 1]
            section_path.append(value)
        blocks.append(
            DocumentBlock(
                block_id=f"{document_id}:b-{ordinal:06d}",
                type=block_type,  # type: ignore[arg-type]
                level=level,
                text=value,
                ordinal=ordinal,
                section_path=list(section_path),
                source_locator={"source": source_name, "line": line_no},
            )
        )
    return blocks


def parse_plain_text(path: Path, document_id: str, *, encoding: str = "utf-8") -> list[DocumentBlock]:
    return parse_text(path.read_text(encoding=encoding, errors="replace"), document_id, source_name=path.name)
