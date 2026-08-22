"""Async research scraper — streaming, stateless, httpx-only (spec §21, §22).

Contract:
- the scraper NEVER creates task ids and NEVER writes progress to its own
  SQLite; it is a pure async generator of ScrapedDocument
- all HTTP goes through httpx.AsyncClient (aiohttp session.get is forbidden)
- rate limiting comes from the source config

The ARQ worker (app/workers/research_jobs.py) owns progress, status,
current_url, error handling and PostgreSQL persistence.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import httpx

from app.research.models import ResearchSourceConfig, ScrapedDocument
from app.research.parser import ResearchParser

logger = logging.getLogger("novelforge.research.scraper")

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
USER_AGENT = "NovelForge Research/2.0 (+https://novelforge.local)"
MAX_PAGES_PER_TASK = 300
MAX_DOCUMENTS_PER_TASK = 200


class ScrapeError(Exception):
    """Fatal scraping failure (transport or parse level)."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class ResearchScraper:
    """Streaming scraper for one source configuration."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                follow_redirects=True,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._owns_client and self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def scrape(
        self,
        *,
        source: ResearchSourceConfig,
        start_url: str,
    ) -> AsyncIterator[ScrapedDocument]:
        """Yield documents discovered from start_url.

        Raises ScrapeError on fatal failures. Individual page failures are
        logged and skipped (bounded by MAX_PAGES_PER_TASK).
        """
        parser = ResearchParser(source)
        client = await self._get_client()
        rate_limit = source.rate_limit or 0.0
        delay = 1.0 / rate_limit if rate_limit > 0 else 0.0

        visited: set[str] = set()
        queue: list[str] = [start_url]
        ordinal = 0
        pages = 0
        consecutive_errors = 0

        while queue and pages < MAX_PAGES_PER_TASK and ordinal < MAX_DOCUMENTS_PER_TASK:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            pages += 1

            html = await self._fetch(client, url, source)
            if html is None:
                consecutive_errors += 1
                if url == start_url:
                    raise ScrapeError(
                        "START_URL_FETCH_FAILED",
                        f"start url unreachable: {url}",
                    )
                if consecutive_errors >= 5:
                    raise ScrapeError(
                        "FETCH_CONSECUTIVE_FAILURES",
                        f"{consecutive_errors} in a row, last={url}",
                    )
                continue
            consecutive_errors = 0

            page = parser.parse_chapter_detail(html, url)

            chapter_links = parser.parse_chapter_list(html, url)
            for link in chapter_links:
                if link["url"] not in visited:
                    queue.append(link["url"])

            next_page = parser.find_next_page(html, url)
            if next_page and next_page not in visited:
                queue.append(next_page)

            quality_ok = page.quality is not None and page.quality.ok
            meaningful = page.content and len(page.content) >= 200
            if quality_ok and meaningful:
                ordinal += 1
                yield ScrapedDocument.build(
                    ordinal=ordinal - 1,
                    title=page.title,
                    source_url=url,
                    content=page.content,
                )
            elif page.content and not quality_ok:
                logger.debug("skip low-quality page %s: %s", url, page.errors)

            if delay:
                await asyncio.sleep(delay)

        if parser.errors:
            logger.warning("parser errors for source %s: %s", source.code, parser.errors[:5])

    async def _fetch(self, client: httpx.AsyncClient, url: str, source: ResearchSourceConfig) -> str | None:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.info("fetch failed %s: %s", url, e)
            return None
        encoding = source.encoding or "utf-8"
        try:
            response.encoding = encoding
            return response.text
        except (LookupError, UnicodeDecodeError):
            return response.text
