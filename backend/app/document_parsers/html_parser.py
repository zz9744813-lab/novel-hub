"""HTML parser preserving semantic elements without flattening the document."""
from __future__ import annotations

from pathlib import Path

from .base import DocumentBlock


def parse_html(path: Path, document_id: str) -> list[DocumentBlock]:
    from html.parser import HTMLParser

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.blocks: list[DocumentBlock] = []
            self.current_tag: str | None = None
            self.current: list[str] = []
            self.ordinal = 0
            self.section_path: list[str] = []
            self.skip_depth = 0
            self.table_index = -1
            self.row_index = -1

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            if tag in {"script", "style", "noscript", "template"}:
                self.skip_depth += 1
                return
            if self.skip_depth:
                return
            if tag == "table":
                self.table_index += 1
                self._flush("table", {"table": self.table_index})
            elif tag == "tr":
                self.row_index += 1
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre", "td", "th"}:
                self.current_tag = tag
                self.current = []

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag in {"script", "style", "noscript", "template"}:
                self.skip_depth = max(0, self.skip_depth - 1)
                return
            if self.skip_depth:
                return
            if self.current_tag and tag == self.current_tag:
                locator = {"tag": tag, "table": self.table_index if self.table_index >= 0 else None}
                if tag in {"td", "th"}:
                    locator["row"] = self.row_index
                block_type = "heading" if tag.startswith("h") else "list" if tag == "li" else "quote" if tag == "blockquote" else "code" if tag == "pre" else "paragraph"
                self._flush(block_type, locator, int(tag[1:]) if tag.startswith("h") else None)
                self.current_tag = None
                self.current = []
            if tag == "tr":
                self.row_index = -1

        def handle_data(self, data):
            if not self.skip_depth and self.current_tag:
                self.current.append(data)

        def _flush(self, block_type, locator, level=None):
            text = " ".join("".join(self.current).split()) if self.current else ""
            if not text and block_type != "table":
                return
            self.ordinal += 1
            if block_type == "heading" and level:
                self.section_path[:] = self.section_path[: level - 1]
                self.section_path.append(text)
            self.blocks.append(
                DocumentBlock(
                    block_id=f"{document_id}:b-{self.ordinal:06d}",
                    type=block_type,
                    level=level,
                    text=text,
                    ordinal=self.ordinal,
                    section_path=list(self.section_path),
                    source_locator=locator,
                )
            )

    parser = Parser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    return parser.blocks
