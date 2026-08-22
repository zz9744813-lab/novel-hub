"""Research parser — CSS selector extraction with quality validation (spec §24).

Keeps the BeautifulSoup CSS-selector approach from the workbench prototype,
adds:
- fallback selector candidates before giving up
- validate_extracted_text quality gate — selector failure NEVER silently
  degrades to "entire body = content"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from app.research.models import ExtractionQuality, ResearchSourceConfig

# Common content containers used as fallback candidates (§24).
FALLBACK_CONTENT_SELECTORS = (
    "#content",
    "div#content",
    "div.content",
    "article",
    "div.chapter-content",
    "div.read-content",
    "div.noveltext",
    "div#chaptercontent",
)

NAV_PATTERNS = re.compile(
    r"(首页|目录|下一[页章]|上一[页章]|返回|书页|设置|举报|加入书签|最新章节|推荐票|"
    r"首页\s*›|版权所有|免责声明|阅读全文|APP|下载|手机阅读)",
)
HTML_NOISE_PATTERNS = re.compile(r"<[a-zA-Z/][^>]*>|\{.*?\}|javascript:|<!--|-->")


def validate_extracted_text(
    text: str,
    *,
    min_chars: int = 200,
    reference_texts: list[str] | None = None,
) -> ExtractionQuality:
    """Quality gate for extracted content (spec §24).

    Indicators:
    - min_chars: absolute content floor
    - navigation_noise_ratio: nav boilerplate lines / total lines
    - duplicate_ratio: duplicate lines within the document
    - html_noise_ratio: residual markup / script leakage
    """
    text = (text or "").strip()
    if not text:
        return ExtractionQuality(
            ok=False, min_chars=min_chars,
            reasons=["empty_content"],
        )

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ExtractionQuality(
            ok=False, min_chars=min_chars, reasons=["empty_content"],
        )

    reasons: list[str] = []
    char_count = len(text)

    nav_lines = sum(1 for ln in lines if NAV_PATTERNS.search(ln))
    nav_ratio = nav_lines / len(lines)
    if nav_ratio > 0.35:
        reasons.append(f"navigation_noise:{nav_ratio:.2f}")

    seen: set[str] = set()
    dup_lines = 0
    for ln in lines:
        if len(ln) >= 12:
            if ln in seen:
                dup_lines += 1
            else:
                seen.add(ln)
    dup_ratio = dup_lines / len(lines)
    if dup_ratio > 0.25:
        reasons.append(f"duplicate:{dup_ratio:.2f}")

    html_hits = len(HTML_NOISE_PATTERNS.findall(text))
    html_ratio = html_hits / max(len(lines), 1)
    if html_ratio > 0.1:
        reasons.append(f"html_noise:{html_ratio:.2f}")

    if char_count < min_chars:
        reasons.append(f"too_short:{char_count}<{min_chars}")

    if reference_texts:
        joined = "\n".join(reference_texts)
        # cross-document boilerplate: identical lines repeated in other pages
        common = sum(1 for ln in seen if len(ln) >= 15 and ln in joined)
        if common > 0 and common / max(len(lines), 1) > 0.3:
            reasons.append("cross_page_boilerplate")

    return ExtractionQuality(
        ok=not reasons,
        min_chars=min_chars,
        char_count=char_count,
        navigation_noise_ratio=round(nav_ratio, 4),
        duplicate_ratio=round(dup_ratio, 4),
        html_noise_ratio=round(html_ratio, 4),
        reasons=reasons,
    )


@dataclass
class ParsedPage:
    url: str
    title: str = ""
    content: str = ""
    chapter_links: list[dict] = field(default_factory=list)
    next_page_url: str | None = None
    errors: list[str] = field(default_factory=list)
    quality: ExtractionQuality | None = None


class ResearchParser:
    """CSS-selector parser for one source configuration."""

    def __init__(self, source: ResearchSourceConfig):
        self.source = source
        self.errors: list[str] = []

    # ── chapter list ──────────────────────────────────────────────
    def parse_chapter_list(self, html: str, page_url: str) -> list[dict[str, str]]:
        selector = self.source.chapter_list_selector
        if not selector:
            return []
        soup = BeautifulSoup(html, "html.parser")
        try:
            items = soup.select(selector)
        except Exception as e:
            self.errors.append(f"chapter_list_selector invalid: {e}")
            return []

        results: list[dict[str, str]] = []
        for item in items[:200]:
            if not isinstance(item, Tag):
                continue
            url = self._extract_href(item)
            if not url:
                link = item.find("a")
                if isinstance(link, Tag):
                    url = self._extract_href(link)
            if not url:
                continue
            url = self._absolutize(url, page_url)
            title = item.get_text(strip=True)
            if not title and isinstance(item, Tag):
                heading = item.find(["h1", "h2", "h3", "b", "strong"])
                if heading is not None:
                    title = heading.get_text(strip=True)
            if url and title:
                results.append({"url": url, "title": title})
        return results

    # ── chapter detail ────────────────────────────────────────────
    def parse_chapter_detail(self, html: str, page_url: str) -> ParsedPage:
        soup = BeautifulSoup(html, "html.parser")
        page = ParsedPage(url=page_url)

        title = ""
        if self.source.title_selector:
            try:
                el = soup.select_one(self.source.title_selector)
            except Exception as e:
                self.errors.append(f"title_selector invalid: {e}")
                el = None
            if el is not None:
                title = el.get_text(strip=True)
        if not title:
            head = soup.find("h1") or soup.find("title")
            if head is not None:
                title = head.get_text(strip=True)
        page.title = title

        content, used_selector = self._extract_content(soup)
        if content is None:
            page.errors.append("content_selector_no_match")
            page.quality = ExtractionQuality(
                ok=False, reasons=["content_selector_no_match"],
            )
            return page

        page.content = content
        page.quality = validate_extracted_text(content)
        if not page.quality.ok:
            page.errors.append(
                f"quality_gate:{','.join(page.quality.reasons)}"
            )
        if used_selector != self.source.content_selector:
            page.errors.append(f"fallback_selector_used:{used_selector}")
        return page

    # ── pagination ────────────────────────────────────────────────
    def find_next_page(self, html: str, page_url: str) -> str | None:
        if not self.source.pagination_selector:
            return None
        soup = BeautifulSoup(html, "html.parser")
        try:
            el = soup.select_one(self.source.pagination_selector)
        except Exception:
            return None
        if not isinstance(el, Tag):
            return None
        href = self._extract_href(el)
        if not href:
            return None
        return self._absolutize(href, page_url)

    # ── internals ─────────────────────────────────────────────────
    def _extract_content(self, soup: BeautifulSoup) -> tuple[str | None, str]:
        """Configured selector first, then fallback candidates (§24).

        Returns (content, used_selector); (None, "") when nothing matched —
        the caller treats that as a parse error, never as body text.
        """
        candidates: list[str] = []
        if self.source.content_selector:
            candidates.append(self.source.content_selector)
        for sel in FALLBACK_CONTENT_SELECTORS:
            if sel not in candidates:
                candidates.append(sel)

        for sel in candidates:
            try:
                els = soup.select(sel)
            except Exception as e:
                self.errors.append(f"selector '{sel}' invalid: {e}")
                continue
            text = "\n\n".join(
                el.get_text(separator="\n", strip=True) for el in els if el.get_text(strip=True)
            ).strip()
            if text:
                return text, sel
        return None, ""

    def _extract_href(self, el: Tag) -> str | None:
        href = el.get("href") or el.get("src")
        if isinstance(href, list):
            href = href[0] if href else None
        return str(href) if href else None

    def _absolutize(self, url: str, page_url: str) -> str:
        if url.startswith(("http://", "https://")):
            return url
        if url.startswith("//"):
            return "https:" + url
        try:
            from urllib.parse import urljoin

            return urljoin(page_url or self.source.base_url, url)
        except Exception:
            base = (page_url or self.source.base_url).rstrip("/")
            return f"{base}/{url.lstrip('/')}"
