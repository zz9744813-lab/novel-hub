"""v9.1 PR-10: REAL end-to-end research chain against a live HTTP fixture site.

No scraper mocks: a real http.server serves tests/fixtures/research_site,
the real ResearchScraper fetches over HTTP, the real ARQ job function
persists documents (DB layer faked), the real exporter writes a TXT file,
and the real reference service creates ReferenceSample rows on disk.
"""
from __future__ import annotations

import asyncio
import functools
import gzip
import http.server
import socket
import threading
import uuid
from pathlib import Path

import pytest

import app.workers.research_jobs as jobs
from app.models import ReferenceSample, ResearchSource, ResearchTask
from app.research.exporter import export_task_txt
from app.research.models import ResearchSourceConfig
from app.research.scraper import ResearchScraper
from app.services.reference_service import create_reference_sample_from_text
from app.workers.research_jobs import run_research_task

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "research_site"


@pytest.fixture(scope="module")
def fixture_site():
    """Serve the fixture site on an ephemeral local port."""
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(FIXTURE_DIR)
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _fixture_source(base: str) -> ResearchSourceConfig:
    return ResearchSourceConfig(
        code="fixturesite",
        name="Fixture 测试书源",
        base_url=base,
        chapter_list_selector="ul.chapters a",
        title_selector="h1",
        content_selector="div#content",
        pagination_selector=None,
        encoding="utf-8",
        rate_limit=0,
        verification_status="verified",
    )


def _run(coro):
    return asyncio.run(coro)


class E2EFakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        if isinstance(self._row, list):
            return self._row
        return [self._row]


class E2ESession:
    """Routes task/source/document lookups against an in-memory store."""

    def __init__(self, store: dict):
        self.store = store
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        from app.models import ResearchDocument

        cols = getattr(stmt, "column_descriptions", [])
        entity = cols[0].get("entity") if cols else None
        if entity is ResearchTask:
            return E2EFakeResult(self.store["task"])
        if entity is ResearchSource:
            return E2EFakeResult(self.store["source"])
        if entity is ResearchDocument:
            vals = {}
            wc = getattr(stmt, "whereclause", None)
            crits = getattr(wc, "clauses", None) or ([wc] if wc else [])
            for crit in crits:
                name = getattr(getattr(crit, "left", None), "name", None)
                if name:
                    vals[name] = getattr(getattr(crit, "right", None), "value", None)
            for d in self.store["docs"]:
                if (
                    str(d.task_id) == str(vals.get("task_id"))
                    and d.source_url == vals.get("source_url")
                ):
                    return E2EFakeResult(d)
            return E2EFakeResult(None)
        if entity is ReferenceSample:
            return E2EFakeResult(None)
        return E2EFakeResult(None)

    def add(self, obj):
        from app.models import ResearchDocument

        self.added.append(obj)
        if isinstance(obj, ResearchDocument):
            self.store["docs"].append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass


class E2EFactory:
    def __init__(self, store: dict):
        self.store = store

    def __call__(self):
        return E2ESession(self.store)


class TestRealScraperE2E:
    def test_scrapes_three_chapters_over_real_http(self, fixture_site):
        source = _fixture_source(fixture_site)
        scraper = ResearchScraper()

        async def collect():
            docs = []
            async for doc in scraper.scrape(source=source, start_url=f"{fixture_site}/index.html"):
                docs.append(doc)
            await scraper.close()
            return docs

        docs = _run(collect())
        assert [d.title for d in docs] == ["第一章 渡口", "第二章 灯下", "第三章 归人"]
        assert [d.ordinal for d in docs] == [0, 1, 2]
        for d in docs:
            assert len(d.content) >= 200
            # content is chapter prose, not nav/footer noise
            assert "版权所有" not in d.content
            assert "目录" not in d.content
        assert "老艄公" in docs[0].content
        assert "半块玉" in docs[1].content
        assert "炊烟" in docs[2].content


class TestFullChainE2E:
    def test_worker_export_reference_chain(self, fixture_site, tmp_path, monkeypatch):
        """HTTP fixture → ARQ job → documents → real TXT → ReferenceSample."""
        monkeypatch.setenv("RESEARCH_EXPORT_ROOT", str(tmp_path / "exports"))
        monkeypatch.setenv("REFERENCE_STORAGE_ROOT", str(tmp_path / "refs"))

        source_row = ResearchSource(
            id=uuid.uuid4(),
            code="fixturesite",
            name="Fixture 测试书源",
            base_url=fixture_site,
            chapter_list_selector="ul.chapters a",
            title_selector="h1",
            content_selector="div#content",
            pagination_selector=None,
            encoding="utf-8",
            rate_limit=0,
            enabled=True,
            verification_status="verified",
            config_json={},
        )
        task = ResearchTask(
            id=uuid.uuid4(),
            book_id=None,
            source_id=source_row.id,
            target_url=f"{fixture_site}/index.html",
            status="queued",
        )
        store = {"task": task, "source": source_row, "docs": []}
        monkeypatch.setattr(jobs, "async_session_factory", E2EFactory(store))

        # 1) real worker job (real scraper, real HTTP) — only the DB layer is faked
        result = _run(run_research_task(None, str(task.id)))
        assert result["ok"] is True
        assert result["status"] == "completed"
        assert result["completed"] == 3

        assert task.status == "completed"
        assert task.progress == 100
        assert task.completed_count == 3
        assert len(store["docs"]) == 3
        contents = [d.content for d in store["docs"]]
        assert all(len(c) >= 200 for c in contents)

        # 2) real export → real file on disk
        export = _run(export_task_txt(E2ESession(store), task=task, documents=store["docs"]))
        export_path = Path(export.file_path)
        assert export_path.is_file()
        assert export_path.stat().st_size == export.byte_size > 0
        export_text = export_path.read_text(encoding="utf-8")
        for d in store["docs"]:
            assert d.title in export_text
            assert d.content in export_text

        # 3) real import → ReferenceSample rows + DeepStudy-readable gzip files
        book_id = uuid.uuid4()
        ref_session = E2ESession(store)
        sample_ids = []
        for d in store["docs"]:
            sample, created = _run(
                create_reference_sample_from_text(
                    ref_session,
                    book_id=book_id,
                    text=d.content,
                    filename=f"fixturesite_{str(task.id)[:8]}_{d.ordinal:04d}.txt",
                )
            )
            assert created is True
            sample_ids.append(sample.id)
            assert sample.status == "ready"
            assert sample.created_by == "research"
            path = Path(sample.storage_path)
            assert path.is_file()
            # DeepStudy reads gzip text
            assert gzip.decompress(path.read_bytes()).decode("utf-8") == d.content

        assert len(sample_ids) == 3
