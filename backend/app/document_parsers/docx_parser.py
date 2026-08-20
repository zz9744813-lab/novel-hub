"""DOCX parser that preserves paragraph/table order and source locators."""
from __future__ import annotations

from pathlib import Path

from .base import DocumentBlock


def _style_level(style_name: str | None) -> int | None:
    if not style_name:
        return None
    lower = style_name.lower().replace(" ", "")
    if lower.startswith("heading"):
        try:
            return int(lower.removeprefix("heading"))
        except ValueError:
            return None
    return None


def parse_docx(path: Path, document_id: str) -> list[DocumentBlock]:
    from docx import Document
    from docx.document import Document as DocumentType
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph
    from docx.oxml.text.paragraph import CT_P
    from docx.oxml.table import CT_Tbl

    doc: DocumentType = Document(str(path))
    blocks: list[DocumentBlock] = []
    ordinal = 0
    section_path: list[str] = []

    def add(block_type: str, text: str, locator: dict, level: int | None = None, metadata: dict | None = None):
        nonlocal ordinal, section_path
        value = (text or "").strip()
        if not value and block_type not in {"table", "table_row"}:
            return
        ordinal += 1
        if block_type == "heading" and level:
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
                source_locator=locator,
                metadata=metadata or {},
            )
        )

    def walk(parent):
        for child in parent.element.body.iterchildren() if isinstance(parent, DocumentType) else parent._tc.iterchildren():
            if isinstance(child, CT_P):
                paragraph = Paragraph(child, parent if isinstance(parent, _Cell) else doc)
                text = paragraph.text.strip()
                if text:
                    style_name = paragraph.style.name if paragraph.style else None
                    level = _style_level(style_name)
                    p_type = "heading" if level else "paragraph"
                    add(p_type, text, {"paragraph": len(blocks) + 1, "style": style_name})
            elif isinstance(child, CT_Tbl):
                table = Table(child, parent if isinstance(parent, _Cell) else doc)
                table_index = sum(1 for b in blocks if b.source_locator.get("table") is not None)
                add("table", "", {"table": table_index}, metadata={"row_count": len(table.rows), "column_count": len(table.columns)})
                for row_index, row in enumerate(table.rows):
                    cells = [cell.text.strip() for cell in row.cells]
                    add(
                        "table_row",
                        " | ".join(cells),
                        {"table": table_index, "row": row_index, "cells": list(range(len(cells)))},
                        metadata={"cells": cells},
                    )

    walk(doc)
    return blocks
