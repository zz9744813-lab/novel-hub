"""Generic web extractor (spec §15) — Trafilatura for topic research.

Used for open-topic research (encyclopedia/news/blog/tutorial/general articles),
NOT as a replacement for verified novel-site adapters. Trafilatura is optional;
on import failure or extraction error we fall back to BeautifulSoup article/main.
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger("novelforge.research.generic")

try:
    import trafilatura  # type: ignore

    _TRAFILATURA = True
except Exception:  # pragma: no cover - optional dependency
    trafilatura = None
    _TRAFILATURA = False


def extract_generic(html: str, url: str | None = None) -> dict:
    """Extract title / author / published_at / main_text / metadata."""
    if _TRAFILATURA and trafilatura is not None:
        try:
            doc = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=False,
                output_format="json",
                with_metadata=True,
                url=url,
            )
            if doc:
                d = json.loads(doc)
                text = d.get("text") or ""
                return {
                    "title": d.get("title") or "",
                    "author": d.get("author") or "",
                    "published_at": d.get("date") or "",
                    "main_text": text,
                    "metadata": {
                        k: v
                        for k, v in d.items()
                        if k not in ("text", "title", "author", "date")
                    },
                    "quality": 1.0 if len(text) >= 200 else round(len(text) / 200.0, 3),
                    "extractor": "trafilatura",
                }
        except Exception as e:  # pragma: no cover
            logger.debug("trafilatura extract failed: %s", e)

    return _bs4_fallback(html)


def _bs4_fallback(html: str) -> dict:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    title_el = soup.find("h1") or soup.find("title")
    title = title_el.get_text(strip=True) if title_el else ""
    main = soup.find("article") or soup.find("main") or soup.find("body")
    text = main.get_text(separator="\n", strip=True) if main else ""
    return {
        "title": title,
        "author": "",
        "published_at": "",
        "main_text": text,
        "metadata": {},
        "quality": 1.0 if len(text) >= 200 else round(len(text) / 200.0, 3),
        "extractor": "bs4",
    }
