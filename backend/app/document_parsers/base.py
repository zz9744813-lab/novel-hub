"""Common document block model used by every import parser."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


BlockType = Literal[
    "heading",
    "paragraph",
    "list",
    "table",
    "table_row",
    "quote",
    "code",
]


@dataclass
class DocumentBlock:
    block_id: str
    type: BlockType
    level: int | None
    text: str
    ordinal: int
    section_path: list[str] = field(default_factory=list)
    source_locator: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
