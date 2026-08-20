"""Small RTF-to-text parser for imported writing plans."""
from __future__ import annotations

import re
from pathlib import Path

from .plain_text import parse_text


def rtf_to_text(raw: str) -> str:
    text = raw.replace("\\par", "\n").replace("\\line", "\n")
    text = re.sub(r"\\'[0-9a-fA-F]{2}", "", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = text.replace("{", "").replace("}", "")
    return text


def parse_rtf(path: Path, document_id: str):
    return parse_text(rtf_to_text(path.read_text(encoding="latin-1", errors="replace")), document_id, source_name=path.name)
