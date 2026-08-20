"""PDF parser with page locators and explicit OCR-required markers."""
from __future__ import annotations

from pathlib import Path

from .base import DocumentBlock


def parse_pdf(path: Path, document_id: str) -> list[DocumentBlock]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    blocks: list[DocumentBlock] = []
    ordinal = 0
    for page_index, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").replace("\r", "\n")
        if not text.strip():
            ordinal += 1
            blocks.append(
                DocumentBlock(
                    block_id=f"{document_id}:b-{ordinal:06d}",
                    type="paragraph",
                    level=None,
                    text="",
                    ordinal=ordinal,
                    source_locator={"page": page_index},
                    metadata={"requires_ocr": True},
                )
            )
            continue
        for line in text.split("\n"):
            value = line.strip()
            if not value:
                continue
            ordinal += 1
            blocks.append(
                DocumentBlock(
                    block_id=f"{document_id}:b-{ordinal:06d}",
                    type="paragraph",
                    level=None,
                    text=value,
                    ordinal=ordinal,
                    source_locator={"page": page_index},
                    metadata={"requires_ocr": False},
                )
            )
    return blocks
