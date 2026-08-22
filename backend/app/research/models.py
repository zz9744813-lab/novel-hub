"""Pydantic contracts for the research production module (spec §21, §23, §24)."""
from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field


class ResearchSourceConfig(BaseModel):
    """Runtime source configuration passed to the scraper (spec §21).

    Mirrors research_sources.json entries / ResearchSource rows.
    """

    code: str = Field(min_length=1)
    name: str
    base_url: str
    chapter_list_selector: str | None = None
    title_selector: str | None = None
    content_selector: str = Field(min_length=1)
    pagination_selector: str | None = None
    encoding: str = "utf-8"
    rate_limit: float = Field(default=0.5, ge=0)
    verification_status: str = "experimental"
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_json_entry(cls, entry: dict) -> "ResearchSourceConfig":
        known = {
            "code", "name", "base_url", "chapter_list_selector", "title_selector",
            "content_selector", "pagination_selector", "encoding", "rate_limit",
            "verification_status",
        }
        return cls(
            code=str(entry.get("code") or entry.get("name") or ""),
            name=str(entry.get("name") or entry.get("code") or ""),
            base_url=str(entry.get("base_url") or ""),
            chapter_list_selector=entry.get("chapter_list_selector"),
            title_selector=entry.get("title_selector"),
            content_selector=str(entry.get("content_selector") or "body"),
            pagination_selector=entry.get("pagination_selector"),
            encoding=str(entry.get("encoding") or "utf-8"),
            rate_limit=float(entry.get("rate_limit") or 0.5),
            verification_status=str(entry.get("verification_status") or "experimental"),
            extra={k: v for k, v in entry.items() if k not in known},
        )


class ScrapedDocument(BaseModel):
    """One fully-extracted document yielded by the scraper (spec §23).

    Content is ALWAYS the complete text — never just a summary or word count.
    """

    ordinal: int = Field(ge=0)
    title: str
    source_url: str
    content: str = Field(min_length=1)
    char_count: int = Field(ge=0)
    content_hash: str

    @classmethod
    def build(cls, *, ordinal: int, title: str, source_url: str, content: str) -> "ScrapedDocument":
        return cls(
            ordinal=ordinal,
            title=title or f"未命名文档 {ordinal + 1}",
            source_url=source_url,
            content=content,
            char_count=len(content),
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )


class ExtractionQuality(BaseModel):
    """Content-quality validation result (spec §24).

    A document is acceptable when ok=True. Indicators feed the error detail
    so the worker can distinguish "selector matched junk" from "site down".
    """

    ok: bool
    min_chars: int = 200
    char_count: int = 0
    navigation_noise_ratio: float = 0.0
    duplicate_ratio: float = 0.0
    html_noise_ratio: float = 0.0
    reasons: list[str] = Field(default_factory=list)
