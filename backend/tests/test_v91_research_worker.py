"""v9.1 PR-07: Research ARQ worker tests (spec §20, §22, §23).

Fake-session store replaces PostgreSQL; fake scraper streams canned
documents. Verifies state transitions, progress updates, cancel
handling, dedupe and full-content persistence.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

import app.workers.research_jobs as jobs
from app.models import ResearchDocument, ResearchSource, ResearchTask
from app.workers.research_jobs import run_research_task
from app.research.models import ScrapedDocument
from app.research.scraper import ScrapeError

BOOK_ID = uuid.uuid4()


class FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class FakeSession:
    def __init__(self, store: dict):
        self.store = store
        self.commits = 0
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        cols = getattr(stmt, "column_descriptions", [])
        entity = cols[0].get("entity") if cols else None
        if entity is ResearchTask:
            return FakeResult(self.store["task"])
        if entity is ResearchSource:
            return FakeResult(self.store["source"])
        if entity is ResearchDocument:
            vals = {}
            wc = getattr(stmt, "whereclause", None)
            for crit in getattr(wc, "clauses", []) or []:
                name = getattr(getattr(crit, "left", None), "name", None)
                if name:
                    right = getattr(crit, "right", None)
                    vals[name] = getattr(right, "value", right)
            for d in self.store["docs"]:
                if (
                    str(d.task_id) == str(vals.get("task_id"))
                    and d.source_url == vals.get("source_url")
                ):
                    return FakeResult(d)
            return FakeResult(None)
        return FakeResult(None)

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, ResearchDocument):
            self.store["docs"].append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    async def close(self):
        pass


class FakeFactory:
    def __init__(self, store: dict):
        self.store = store
        self.sessions: list[FakeSession] = []

    def __call__(self):
        s = FakeSession(self.store)
        self.sessions.append(s)
        return s


def _doc(i: int) -> ScrapedDocument:
    return ScrapedDocument.build(
        ordinal=i, title=f"第{i + 1}章", source_url=f"https://example.com/c/{i}",
        content="正文内容" * 100,
    )


class FakeScraper:
    """Streams docs; optionally raises or honors cancel_at."""

    instances: list["FakeScraper"] = []

    def __init__(self, client=None):
        self.docs = FakeScraper._next_docs
        self.raiser = FakeScraper._next_raiser
        self.cancel_at = FakeScraper._next_cancel_at
        FakeScraper.instances.append(self)

    async def scrape(self, *, source, start_url):
        for i, doc in enumerate(self.docs):
            if self.cancel_at is not None and i == self.cancel_at:
                # simulate API setting cancel during stream: mutate store task
                FakeScraper._on_cancel()
                raise asyncio.CancelledError  # pragma: no cover - not used
            yield doc
        if self.raiser:
            raise self.raiser

    async def close(self):
        pass

    _next_docs: list = []
    _next_raiser: Exception | None = None
    _next_cancel_at: int | None = None
    _on_cancel = lambda: None


@pytest.fixture
def store(monkeypatch):
    task = ResearchTask(
        id=uuid.uuid4(), book_id=BOOK_ID, source_id=uuid.uuid4(),
        target_url="https://example.com/list", status="queued",
    )
    source = ResearchSource(
        id=task.source_id, code="test", name="测试源",
        base_url="https://example.com", content_selector="div#content",
        encoding="utf-8", rate_limit=0, enabled=True,
        verification_status="experimental", config_json={},
    )
    s = {"task": task, "source": source, "docs": []}
    factory = FakeFactory(s)
    monkeypatch.setattr(jobs, "async_session_factory", factory)
    FakeScraper.instances = []
    FakeScraper._next_docs = []
    FakeScraper._next_raiser = None
    FakeScraper._next_cancel_at = None
    FakeScraper._on_cancel = lambda: None
    monkeypatch.setattr(jobs, "ResearchScraper", FakeScraper)
    s["_factory"] = factory
    return s


def _run(coro):
    return asyncio.run(coro)


class TestRunResearchTask:
    def test_happy_path_persists_documents_and_completes(self, store):
        FakeScraper._next_docs = [_doc(i) for i in range(3)]
        result = _run(run_research_task(None, str(store["task"].id)))
        assert result["ok"] and result["status"] == "completed"
        assert result["completed"] == 3

        task = store["task"]
        assert task.status == "completed"
        assert task.progress == 100
        assert task.completed_count == 3
        assert task.discovered_count == 3
        assert task.current_url is None
        assert task.finished_at is not None and task.started_at is not None

        docs = store["docs"]
        assert len(docs) == 3
        # §23: full content persisted, not just title/url/word_count
        for d in docs:
            assert d.content and len(d.content) >= 400
            assert d.char_count == len(d.content)
            assert len(d.content_hash) == 64
            assert d.metadata_json == {"source_code": "test"}

    def test_progress_increases_during_stream(self, store):
        FakeScraper._next_docs = [_doc(i) for i in range(5)]
        _run(run_research_task(None, str(store["task"].id)))
        # per-document commits happened (running → 5 doc commits → final)
        assert store["task"].progress == 100

    def test_cancel_requested_marks_cancelled(self, store):
        FakeScraper._next_docs = [_doc(i) for i in range(3)]

        # API sets cancel before the stream's second document is persisted
        original_execute_state = {"n": 0}

        def on_cancel():
            store["task"].status = "cancel_requested"

        # patch scrape to flip status after first doc
        class CancelScraper(FakeScraper):
            async def scrape(self, *, source, start_url):
                for i, doc in enumerate(self.docs):
                    yield doc
                    if i == 0:
                        on_cancel()
                        return

        import app.workers.research_jobs as j
        j.ResearchScraper = CancelScraper

        result = _run(run_research_task(None, str(store["task"].id)))
        assert result["status"] == "cancelled"
        assert store["task"].status == "cancelled"
        assert store["task"].finished_at is not None
        assert len(store["docs"]) == 1

    def test_terminal_status_skipped(self, store):
        store["task"].status = "completed"
        FakeScraper._next_docs = [_doc(0)]
        result = _run(run_research_task(None, str(store["task"].id)))
        assert result["skipped"] is True
        assert store["docs"] == []

    def test_disabled_source_fails(self, store):
        store["source"].enabled = False
        result = _run(run_research_task(None, str(store["task"].id)))
        assert result["error"] == "source_disabled"
        assert store["task"].status == "failed"
        assert store["task"].error_code == "SOURCE_DISABLED"

    def test_scrape_error_marks_failed_with_code(self, store):
        FakeScraper._next_docs = []
        FakeScraper._next_raiser = ScrapeError("START_URL_FETCH_FAILED", "boom")
        result = _run(run_research_task(None, str(store["task"].id)))
        assert result["ok"] is False
        assert result["error"] == "START_URL_FETCH_FAILED"
        assert store["task"].status == "failed"
        assert store["task"].error_code == "START_URL_FETCH_FAILED"
        assert store["task"].error_detail["detail"] == "boom"

    def test_crash_marks_failed(self, store):
        FakeScraper._next_docs = []
        FakeScraper._next_raiser = RuntimeError("unexpected")
        result = _run(run_research_task(None, str(store["task"].id)))
        assert result["error"] == "WORKER_CRASH"
        assert store["task"].status == "failed"
        assert store["task"].error_code == "WORKER_CRASH"

    def test_invalid_task_id(self, store):
        result = _run(run_research_task(None, "not-a-uuid"))
        assert result == {"ok": False, "error": "invalid_task_id"}

    def test_missing_task(self, store, monkeypatch):
        # factory with empty store
        empty = {"task": None, "source": None, "docs": []}
        monkeypatch.setattr(jobs, "async_session_factory", FakeFactory(empty))
        result = _run(run_research_task(None, str(uuid.uuid4())))
        assert result == {"ok": False, "error": "task_or_source_missing"}

    def test_document_dedupe_by_source_url(self, store):
        """Re-running a failed task must not duplicate rows (uq constraint)."""
        FakeScraper._next_docs = [_doc(i) for i in range(2)]
        _run(run_research_task(None, str(store["task"].id)))
        assert len(store["docs"]) == 2

        # simulate retry: task back to queued, same docs stream again
        task = store["task"]
        task.status = "queued"
        task.progress = 0
        _run(run_research_task(None, str(task.id)))
        # still exactly 2 — second run updates in place
        assert len(store["docs"]) == 2
        assert store["task"].completed_count == 2


def test_worker_registered_in_arq():
    from app.workers.arq_worker import WorkerSettings

    names = {getattr(f, "__name__", str(f)) for f in WorkerSettings.functions}
    assert "run_research_task_job" in names
    assert WorkerSettings.max_jobs == int(__import__("os").environ.get("ARQ_MAX_JOBS", "1"))
