"""Document block extraction — no LLM (v8.0 Phase 1)."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_blocks_from_text(text: str, document_id: str) -> dict[str, Any]:
    """Split plain text into DocumentBlocks (heading/paragraph heuristic)."""
    blocks: list[dict[str, Any]] = []
    ordinal = 0
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        ordinal += 1
        btype = "paragraph"
        level = 0
        m_h = re.match(r"^(#{1,6})\s+", line)
        if m_h:
            btype = "heading"
            level = len(m_h.group(1))
            line = re.sub(r"^#+\s*", "", line)
        elif re.match(r"^(第[一二三四五六七八九十百千0-9]+[卷章部]|Ch\.?\s*\d+|卷\s*\d+)", line, re.I):
            btype = "heading"
            level = 2
        elif re.match(r"^[-*•]\s+", line):
            btype = "list"
            line = re.sub(r"^[-*•]\s+", "", line)
        blocks.append(
            {
                "block_id": f"b-{ordinal:06d}",
                "type": btype,
                "level": level,
                "text": line,
                "page": 1,
                "ordinal": ordinal,
                "source_locator": {"page": 1, "paragraph": ordinal, "table": None, "row": None},
            }
        )
    return {"document_id": document_id, "blocks": blocks}


def extract_file(path: Path, original_filename: str, document_id: str) -> dict[str, Any]:
    """Best-effort extract text from common formats; store as blocks."""
    suffix = path.suffix.lower()
    raw = path.read_bytes()
    text = ""
    if suffix in {".txt", ".md", ".markdown", ".text", ".csv", ".json", ".html", ".htm", ".xml", ".log"}:
        text = raw.decode("utf-8", errors="replace")
        if suffix in {".html", ".htm"}:
            text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
            text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", "\n", text)
    elif suffix == ".docx":
        try:
            import zipfile
            import xml.etree.ElementTree as ET

            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml")
            root = ET.fromstring(xml)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paras = []
            for p in root.findall(".//w:p", ns):
                parts = [t.text or "" for t in p.findall(".//w:t", ns)]
                line = "".join(parts).strip()
                if line:
                    paras.append(line)
            text = "\n".join(paras)
        except Exception as e:
            text = f"[docx extract failed: {e}]"
    elif suffix == ".pdf":
        # Prefer text extract; OCR out of scope Phase 1
        try:
            # try pypdf if present
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(str(path))
            pages = []
            for i, page in enumerate(reader.pages):
                pages.append(page.extract_text() or "")
            text = "\n".join(pages)
        except Exception:
            text = raw.decode("utf-8", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")

    doc = extract_blocks_from_text(text, document_id)
    doc["meta"] = {
        "original_filename": original_filename,
        "char_count": len(text),
        "block_count": len(doc["blocks"]),
        "sha256": sha256_bytes(raw),
    }
    return doc


# Heuristic chatter candidates (Phase 1 — mark only; no auto-delete)
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
    """Rule-only candidates for ImportSanitizerAgent (Phase 1)."""
    out = []
    for b in blocks:
        text = b.get("text") or ""
        classification = "source_content"
        action = "keep"
        reason = "default_keep"
        conf = 0.5
        for pat in CHATTER_PATTERNS:
            if re.search(pat, text):
                classification = "assistant_chatter"
                action = "review"
                reason = f"matched_candidate:{pat}"
                conf = 0.7
                break
        out.append(
            {
                "block_id": b["block_id"],
                "classification": classification,
                "action": action,
                "confidence": conf,
                "reason": reason,
            }
        )
    return out
