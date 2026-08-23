"""Research source probe — test a URL against a source's selector rules (spec §7, §9).

A probe answers: "does this source's current rule set actually extract
meaningful content from this URL right now?" It records evidence (HTTP status,
page type, selector hit counts, extracted chars, anti-bot signals) rather than
just flipping a status string.

This is the "先测试，再运行" primitive behind the [测试该地址] button.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.research.models import ResearchSourceConfig
from app.research.parser import FALLBACK_CONTENT_SELECTORS, ResearchParser
from bs4 import BeautifulSoup, Tag

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
USER_AGENT = "NovelForge Research/2.0 (+https://novelforge.local)"

# Anti-bot / access-control signals we must NOT bypass (spec §16).
_ANTI_BOT_SIGNALS: list[tuple[str, re.Pattern[str]]] = [
    ("captcha", re.compile(r"captcha|验证码|安全验证|拖动|滑块|slide", re.I)),
    ("cloudflare", re.compile(r"cloudflare|cf-challenge|cf_|challenge-platform", re.I)),
    ("login", re.compile(r"请登录|登录后|login|sign\s*in|please\s*sign", re.I)),
    ("paywall", re.compile(r"付费|订阅|购买本章|vip章节|paywall", re.I)),
]


@dataclass
class ProbeResult:
    status: str  # passed / failed / blocked
    http_status: int | None = None
    final_url: str | None = None
    latency_ms: int | None = None
    response_bytes: int | None = None
    page_type: str = "generic"  # book / chapter / generic
    title_hit_count: int = 0
    list_link_count: int = 0
    content_hit_count: int = 0
    extracted_chars: int = 0
    anti_bot_type: str | None = None
    encoding_detected: str | None = None
    diagnostics: list[str] = field(default_factory=list)
    candidate_selectors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "http_status": self.http_status,
            "final_url": self.final_url,
            "latency_ms": self.latency_ms,
            "response_bytes": self.response_bytes,
            "page_type": self.page_type,
            "title_hit_count": self.title_hit_count,
            "list_link_count": self.list_link_count,
            "content_hit_count": self.content_hit_count,
            "extracted_chars": self.extracted_chars,
            "anti_bot_type": self.anti_bot_type,
            "encoding_detected": self.encoding_detected,
            "diagnostics": self.diagnostics,
            "candidate_selectors": self.candidate_selectors,
        }


def _detect_anti_bot(html: str) -> str | None:
    for kind, pattern in _ANTI_BOT_SIGNALS:
        if pattern.search(html):
            return kind
    return None


def _detect_encoding(response: httpx.Response) -> str:
    enc = response.encoding or ""
    if enc and enc.lower() not in ("utf-8", "utf8"):
        return enc
    # peek charset from content-type / meta
    ctype = response.headers.get("content-type", "")
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    if m:
        return m.group(1)
    head = response.text[:2048].lower()
    m = re.search(r'charset=["\']?([\w-]+)', head)
    if m:
        return m.group(1)
    return "utf-8"


def _selector_hits(soup: BeautifulSoup, selector: str | None) -> list[Tag]:
    if not selector:
        return []
    try:
        return [el for el in soup.select(selector) if isinstance(el, Tag)]
    except Exception:
        return []


def _content_chars(soup: BeautifulSoup, selector: str) -> int:
    try:
        els = soup.select(selector)
    except Exception:
        return 0
    return sum(len(el.get_text(strip=True)) for el in els)


async def probe_source(
    *,
    source: ResearchSourceConfig,
    test_url: str,
) -> ProbeResult:
    """Fetch test_url and evaluate the source's selectors against the page."""
    start = time.perf_counter()
    result = ProbeResult(status="failed")

    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    ) as client:
        try:
            response = await client.get(test_url)
        except httpx.HTTPError as e:
            result.diagnostics.append(f"fetch_error:{type(e).__name__}:{e}")
            result.status = "failed"
            result.latency_ms = int((time.perf_counter() - start) * 1000)
            return result

    result.latency_ms = int((time.perf_counter() - start) * 1000)
    result.http_status = response.status_code
    result.final_url = str(response.url)
    result.response_bytes = len(response.content)
    result.encoding_detected = _detect_encoding(response)

    if response.status_code in (401, 403, 429):
        result.status = "blocked"
        result.anti_bot_type = _detect_anti_bot(response.text) or "http_status"
        result.diagnostics.append(f"http_{response.status_code}")
        return result

    html = response.text
    anti_bot = _detect_anti_bot(html)
    if anti_bot:
        result.anti_bot_type = anti_bot

    soup = BeautifulSoup(html, "html.parser")

    # Title
    if source.title_selector:
        title_els = _selector_hits(soup, source.title_selector)
        result.title_hit_count = len(title_els)
    if result.title_hit_count == 0:
        h1 = soup.find("h1") or soup.find("title")
        if h1 is not None:
            result.title_hit_count = 1

    # Chapter list (book page)
    list_els = _selector_hits(soup, source.chapter_list_selector)
    result.list_link_count = len(list_els)

    # Content (chapter page): configured selector, then fallbacks
    parser = ResearchParser(source)
    extracted, used_selector = parser._extract_content(soup)
    if extracted:
        result.content_hit_count = 1
        result.extracted_chars = len(extracted)

    # Candidate selectors for diagnostics when content missing (§9)
    if result.extracted_chars < 200:
        candidates = [source.content_selector] + list(FALLBACK_CONTENT_SELECTORS)
        for sel in candidates:
            if not sel or sel == used_selector:
                continue
            chars = _content_chars(soup, sel)
            if chars > 0:
                result.candidate_selectors.append({"selector": sel, "chars": chars})
        result.candidate_selectors.sort(key=lambda c: -c["chars"])
        result.candidate_selectors = result.candidate_selectors[:5]

    # Page type classification
    if result.list_link_count >= 3 and result.extracted_chars < 500:
        result.page_type = "book"
    elif result.extracted_chars >= 200:
        result.page_type = "chapter"
    else:
        result.page_type = "generic"

    # Final status
    if anti_bot:
        result.status = "blocked"
    elif result.page_type == "book" and result.list_link_count >= 3:
        result.status = "passed"
    elif result.page_type == "chapter" and result.extracted_chars >= 200:
        result.status = "passed"
    else:
        result.status = "failed"

    if result.status == "failed" and not result.diagnostics:
        result.diagnostics.append("selector_no_match")
    if result.candidate_selectors:
        result.diagnostics.append("candidate_selectors_available")

    return result
