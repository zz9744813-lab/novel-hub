"""v9.1 PR-06: Research production module unit tests.

Covers parser quality gate, fallback selectors, scraper streaming contract
(via mock transport), and source seeding — no Postgres required.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from app.research.models import ResearchSourceConfig, ScrapedDocument
from app.research.parser import ResearchParser, validate_extracted_text
from app.research.scraper import ResearchScraper, ScrapeError, MAX_DOCUMENTS_PER_TASK

GOOD_PARAGRAPH = "林深提着灯笼走进巷口，雨水顺着伞骨滴落。" * 30


def _source(**overrides) -> ResearchSourceConfig:
    cfg = {
        "code": "test-site",
        "name": "测试站",
        "base_url": "https://example.com",
        "chapter_list_selector": "ul.chapters a",
        "title_selector": "h1.title",
        "content_selector": "div#content",
        "pagination_selector": "a.next",
        "rate_limit": 0,
    }
    cfg.update(overrides)
    return ResearchSourceConfig(**cfg)


# ── validate_extracted_text (spec §24) ──────────────────────────────

class TestValidateExtractedText:
    def test_good_content_passes(self):
        q = validate_extracted_text(GOOD_PARAGRAPH)
        assert q.ok, q.reasons
        assert q.char_count == len(GOOD_PARAGRAPH)

    def test_empty_fails(self):
        assert not validate_extracted_text("").ok
        assert not validate_extracted_text("   \n  ").ok

    def test_too_short_fails(self):
        q = validate_extracted_text("短文本")
        assert not q.ok
        assert any(r.startswith("too_short") for r in q.reasons)

    def test_navigation_noise_fails(self):
        lines = ["首页", "上一章", "下一章", "目录", "书签"] * 40
        q = validate_extracted_text("\n".join(lines))
        assert not q.ok
        assert any(r.startswith("navigation_noise") for r in q.reasons)

    def test_duplicate_ratio_fails(self):
        line = "这一行重复出现的锅炉文本内容超过十二个字符了"
        q = validate_extracted_text("\n".join([line] * 60), reference_texts=[])
        assert any(r.startswith("duplicate") for r in q.reasons)
        assert not q.ok

    def test_html_noise_fails(self):
        text = GOOD_PARAGRAPH + ("<div class='ad'>点击下载APP</div>\n" * 30)
        q = validate_extracted_text(text)
        assert any(r.startswith("html_noise") for r in q.reasons)

    def test_min_chars_configurable(self):
        q = validate_extracted_text("刚好十个字的内容", min_chars=5)
        assert q.ok


# ── parser ──────────────────────────────────────────────────────────

class TestResearchParser:
    def _list_html(self) -> str:
        return """
        <html><body>
        <ul class="chapters">
            <li><a href="/chapter/1">第一章 起风</a></li>
            <li><a href="/chapter/2">第二章 落雨</a></li>
        </ul>
        </body></html>
        """

    def _detail_html(self, content: str = GOOD_PARAGRAPH) -> str:
        return f"""
        <html><body>
        <h1 class="title">第一章 起风</h1>
        <div id="content">{content}</div>
        <a class="next" href="/chapter/2">下一章</a>
        </body></html>
        """

    def test_parse_chapter_list_resolves_relative(self):
        p = ResearchParser(_source())
        links = p.parse_chapter_list(self._list_html(), "https://example.com/book/1")
        assert len(links) == 2
        assert links[0]["url"] == "https://example.com/chapter/1"
        assert links[0]["title"] == "第一章 起风"

    def test_parse_detail_extracts_title_content(self):
        p = ResearchParser(_source())
        page = p.parse_chapter_detail(self._detail_html(), "https://example.com/chapter/1")
        assert page.title == "第一章 起风"
        assert GOOD_PARAGRAPH[:20] in page.content
        assert page.quality is not None and page.quality.ok

    def test_selector_miss_is_error_not_body(self):
        # §24: selector failure must NOT degrade to whole-body text silently —
        # either a fallback is explicitly reported or the page is a parse error
        p = ResearchParser(_source(content_selector="div#nonexistent"))
        page = p.parse_chapter_detail(self._detail_html(), "https://example.com/chapter/1")
        assert any(
            e.startswith("content_selector_no_match")
            or e.startswith("fallback_selector_used")
            for e in page.errors
        )
        # raw markup must never leak into content
        assert "<h1" not in page.content and "<div" not in page.content

    def test_fallback_selector_used_reported(self):
        # configured selector misses; #content fallback hits
        p = ResearchParser(_source(content_selector="div.wrong-class"))
        page = p.parse_chapter_detail(self._detail_html(), "https://example.com/chapter/1")
        assert any(e.startswith("fallback_selector_used") for e in page.errors)

    def test_find_next_page(self):
        p = ResearchParser(_source())
        assert p.find_next_page(self._detail_html(), "https://example.com/chapter/1") == \
            "https://example.com/chapter/2"

    def test_low_quality_content_flagged(self):
        junk = "首页\n上一章\n下一章\n目录\n" * 30
        p = ResearchParser(_source())
        page = p.parse_chapter_detail(self._detail_html(junk), "https://example.com/c/1")
        assert page.quality is not None and not page.quality.ok
        assert any(e.startswith("quality_gate") for e in page.errors)


# ── ScrapedDocument ─────────────────────────────────────────────────

class TestScrapedDocument:
    def test_build_hashes_content(self):
        doc = ScrapedDocument.build(
            ordinal=0, title="t", source_url="u", content="正文" * 100,
        )
        assert doc.char_count == 200
        assert len(doc.content_hash) == 64
        assert doc.content_hash == ScrapedDocument.build(
            ordinal=0, title="t", source_url="u", content="正文" * 100,
        ).content_hash

    def test_empty_title_gets_default(self):
        doc = ScrapedDocument.build(ordinal=3, title="", source_url="u", content="x" * 300)
        assert doc.title == "未命名文档 4"


# ── scraper streaming contract (mock transport) ─────────────────────

def _make_client(routes: dict[str, str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for pattern, body in routes.items():
            if pattern in str(request.url):
                return httpx.Response(200, text=body)
        return httpx.Response(404, text="not found")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _run(coro):
    return asyncio.run(coro)


class TestResearchScraper:
    def _detail(self, title: str, ordinal: int) -> str:
        return (
            f"<html><body><h1 class='title'>{title}</h1>"
            f"<div id='content'>{GOOD_PARAGRAPH}</div></body></html>"
        )

    async def _collect(self, routes, source):
        client = _make_client(routes)
        try:
            scraper = ResearchScraper(client=client)
            return [d async for d in scraper.scrape(source=source, start_url=source.base_url + "/list")]
        finally:
            await client.aclose()

    def test_streams_documents_in_order(self):
        source = _source(rate_limit=0)
        routes = {
            "/list": "<ul class='chapters'><li><a href='/chapter/1'>第一章</a></li>"
                     "<li><a href='/chapter/2'>第二章</a></li></ul>",
            "/chapter/1": self._detail("第一章", 1),
            "/chapter/2": self._detail("第二章", 2),
        }
        docs = _run(self._collect(routes, source))
        assert [d.title for d in docs] == ["第一章", "第二章"]
        assert [d.ordinal for d in docs] == [0, 1]

    def test_scraper_creates_no_task_state(self):
        # §22: scraper owns no task db / no ids — just verify a source
        # without chapter links yields at most the single page document
        source = _source(rate_limit=0, chapter_list_selector=None)
        routes = {"/single": self._detail("单页", 1)}
        client = _make_client(routes)

        async def collect():
            scraper = ResearchScraper(client=client)
            return [
                d async for d in scraper.scrape(
                    source=source, start_url="https://example.com/single"
                )
            ]

        try:
            docs = _run(collect())
        finally:
            _run(client.aclose())
        assert len(docs) <= 1

    def test_missing_pages_skipped(self):
        source = _source(rate_limit=0)
        routes = {
            "/list": "<ul class='chapters'><li><a href='/chapter/1'>第一章</a></li>"
                     "<li><a href='/missing'>缺失章</a></li></ul>",
            "/chapter/1": self._detail("第一章", 1),
        }
        docs = _run(self._collect(routes, source))
        assert [d.title for d in docs] == ["第一章"]

    def test_consecutive_fetch_failures_raise(self):
        source = _source(rate_limit=0, chapter_list_selector=None)
        # every URL 404s → start url unreachable → ScrapeError
        client = _make_client({})
        async def scenario():
            scraper = ResearchScraper(client=client)
            async for _ in scraper.scrape(source=source, start_url="https://example.com/a"):
                pass
        with pytest.raises(ScrapeError) as exc_info:
            _run(scenario())
        assert exc_info.value.code == "START_URL_FETCH_FAILED"
        _run(client.aclose())

    def test_mid_stream_consecutive_failures_raise(self):
        source = _source(rate_limit=0)
        # 6 chapters, all but the list page 404 → consecutive failure raises
        links = "".join(
            f"<li><a href='/chapter/{i}'>第{i}章</a></li>" for i in range(1, 7)
        )
        routes = {"/list": f"<ul class='chapters'>{links}</ul>"}
        client = _make_client(routes)
        async def scenario():
            scraper = ResearchScraper(client=client)
            async for _ in scraper.scrape(source=source, start_url="https://example.com/list"):
                pass
        with pytest.raises(ScrapeError) as exc_info:
            _run(scenario())
        assert exc_info.value.code == "FETCH_CONSECUTIVE_FAILURES"
        _run(client.aclose())

    def test_max_documents_cap(self):
        # 250 chapter links but cap is MAX_DOCUMENTS_PER_TASK
        links = "".join(
            f"<li><a href='/chapter/{i}'>第{i}章</a></li>"
            for i in range(1, MAX_DOCUMENTS_PER_TASK + 60)
        )
        routes = {
            "/list": f"<ul class='chapters'>{links}</ul>",
        }
        for i in range(1, MAX_DOCUMENTS_PER_TASK + 60):
            routes[f"/chapter/{i}"] = self._detail(f"第{i}章", i)
        source = _source(rate_limit=0)
        docs = _run(self._collect(routes, source))
        assert len(docs) <= MAX_DOCUMENTS_PER_TASK


# ── source config + seeding ─────────────────────────────────────────

class TestResearchSourceConfig:
    def test_from_json_entry(self):
        entry = {
            "code": "qidian", "name": "起点中文网", "base_url": "https://www.qidian.com",
            "content_selector": "div.read-content", "encoding": "utf-8",
            "rate_limit": 1.0, "verification_status": "experimental",
            "description": "desc", "tags": ["novel"],
        }
        cfg = ResearchSourceConfig.from_json_entry(entry)
        assert cfg.code == "qidian"
        assert cfg.extra == {"description": "desc", "tags": ["novel"]}

    def test_config_json_has_entries(self):
        from app.research.seeding import SOURCES_JSON, load_source_entries
        assert SOURCES_JSON.exists(), "sources JSON must ship in repo (RES-004)"
        entries = load_source_entries()
        assert len(entries) >= 12
        codes = {e.get("code") for e in entries}
        assert "qidian" in codes and "jjwxc" in codes
        # every entry must declare a verification status (§18)
        for e in entries:
            assert e.get("verification_status") in ("verified", "experimental", "disabled"), e.get("code")


class TestSeedResearchSources:
    def test_seeding_idempotent_and_protects_verified(self):
        from app.research import seeding

        class _FakeResult:
            def __init__(self, rows):
                self._rows = rows
            def scalars(self):
                return self
            def __iter__(self):
                return iter(self._rows)

        class _FakeDB:
            def __init__(self):
                self.rows: list = []
                self.added: list = []
                self.flushed = False
            async def execute(self, *_a, **_k):
                return _FakeResult(self.rows)
            def add(self, row):
                self.added.append(row)
            async def flush(self):
                self.flushed = True

        from app.models import ResearchSource

        db = _FakeDB()
        report = asyncio.run(seeding.seed_research_sources(db))
        assert report["inserted"] >= 12
        assert db.flushed

        # second run updates instead of duplicating
        db.rows = list(db.added)
        report2 = asyncio.run(seeding.seed_research_sources(db))
        assert report2["inserted"] == 0
        assert report2["updated"] == report["inserted"]

        # a manually verified source keeps its status across reseeding
        verified = db.rows[0]
        verified.verification_status = "verified"
        asyncio.run(seeding.seed_research_sources(db))
        assert verified.verification_status == "verified"
