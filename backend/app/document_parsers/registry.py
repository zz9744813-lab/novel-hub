"""Parser registry and safe dispatch for supported import formats."""
from __future__ import annotations

from pathlib import Path

from .base import DocumentBlock
from .docx_parser import parse_docx
from .html_parser import parse_html
from .pdf_parser import parse_pdf
from .plain_text import parse_plain_text
from .rtf_parser import parse_rtf

TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text", ".csv", ".json", ".log", ".xml"}


def parse_document(path: Path, document_id: str) -> list[DocumentBlock]:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return parse_plain_text(path, document_id)
    if suffix in {".html", ".htm"}:
        return parse_html(path, document_id)
    if suffix == ".docx":
        return parse_docx(path, document_id)
    if suffix == ".pdf":
        return parse_pdf(path, document_id)
    if suffix == ".rtf":
        return parse_rtf(path, document_id)
    raise ValueError(f"unsupported document format: {suffix or '<none>'}")
