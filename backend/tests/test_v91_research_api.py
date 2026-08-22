"""v9.1 PR-08: Research REST API tests (spec §19).

Fake session replaces PostgreSQL; enqueue is monkeypatched. Verifies the
API contract: sources list, task creation with ARQ enqueue (fail-closed),
real task list/detail, cancel semantics, and document access.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.research as research_api
from app.database import get_db
from app.models import (
    ReferenceSample,
    ResearchDocument,
    ResearchExport,
    ResearchSource,
    ResearchTask,
)


def _source(**kw) -> ResearchSource:
    base = dict(
        id=uuid.uuid4(),
        code="qidian",
        name="起点中文网",
        base_url="https://www.qidian.com",
        chapter_list_selector="a",
        title_selector="h1",
        content_selector="div.main",
        pagination_selector=None,
        encoding="utf-8",
        rate_limit=1.0,
        enabled=True,
        verification_status="experimental",
        last_verified_at=None,
        config_json={},
    )
    base.update(kw)
    return ResearchSource(**base)


def _task(source: ResearchSource, **kw) -> ResearchTask:
    base = dict(
        id=uuid.uuid4(),
        book_id=None,
        source_id=source.id,
        target_url="https://example.com/book/1",
        status="queued",
        progress=0,
        discovered_count=0,
        completed_count=0,
        current_url=None,
        error_code=None,
        error_detail=None,
        started_at=None,
        finished_at=None,
    )
    base.update(kw)
    return ResearchTask(**base)


def _doc(task: ResearchTask, ordinal: int = 0) -> ResearchDocument:
    content = f"第{ordinal + 1}章正文" * 100
    import hashlib

    return ResearchDocument(
        id=uuid.uuid4(),
        task_id=task.id,
        book_id=None,
        ordinal=ordinal,
        title=f"第{ordinal + 1}章",
        source_url=f"https://example.com/c/{ordinal}",
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        char_count=len(content),
        metadata_json={"source_code": "qidian"},
    )


class FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalar_one(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        if isinstance(self._row, list):
            return self._row
        return [self._row]


class FakeSession:
    """Routes select() by entity; assigns ids on flush."""

    def __init__(self, store: dict):
        self.store = store
        self.added: list = []
        self.commits = 0

    async def execute(self, stmt):
        cols = getattr(stmt, "column_descriptions", [])
        entities = [c.get("entity") for c in cols]

        if not cols or entities[0] is None:
            from_tables = {
                getattr(f, "name", None) for f in (getattr(stmt, "_from_obj", None) or ())
            }
            if "research_documents" in from_tables:
                return FakeResult(len(self.store.get("docs", [])))
            return FakeResult(len(self.store.get("tasks", [])))

        if len(cols) == 2 and entities[0] is ResearchTask and entities[1] is ResearchSource:
            pairs = [
                (t, self.store["source"])
                for t in self.store.get("tasks", [])
                if str(t.source_id) == str(self.store["source"].id)
            ]
            return FakeResult(pairs)

        if entities[0] is ResearchSource:
            src = self.store["source"]
            wanted = _where_uuids(stmt).get("id")
            if wanted is not None and str(src.id) != str(wanted):
                return FakeResult(None)
            return FakeResult(src)
        if entities[0] is ResearchTask:
            wanted = _where_uuids(stmt).get("id")
            for t in self.store.get("tasks", []):
                if wanted is None or str(t.id) == str(wanted):
                    return FakeResult(t)
            return FakeResult(None)
        if entities[0] is ResearchDocument:
            vals = _where_uuids(stmt)
            docs = self.store.get("docs", [])
            if "id" in vals:
                for d in docs:
                    if str(d.id) == str(vals["id"]):
                        return FakeResult(d)
                return FakeResult(None)
            if "task_id" in vals:
                matched = [d for d in docs if str(d.task_id) == str(vals["task_id"])]
                return FakeResult(matched)
            return FakeResult(docs)
        if entities[0] is ResearchExport:
            vals = _where_uuids(stmt)
            for e in self.store.get("exports", []):
                if str(e.id) == str(vals.get("id")):
                    return FakeResult(e)
            return FakeResult(None)
        if entities[0] is ReferenceSample:
            vals = _where_vals(stmt)
            for s in self.store.get("samples", []):
                if (
                    str(s.book_id) == str(vals.get("book_id"))
                    and s.content_sha256 == vals.get("content_sha256")
                ):
                    return FakeResult(s)
            return FakeResult(None)
        return FakeResult(None)

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, ResearchTask):
            self.store.setdefault("tasks", []).append(obj)
        elif isinstance(obj, ResearchExport):
            self.store.setdefault("exports", []).append(obj)
        elif isinstance(obj, ReferenceSample):
            self.store.setdefault("samples", []).append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    async def refresh(self, obj):
        pass


def _where_vals(stmt) -> dict:
    out: dict = {}
    wc = getattr(stmt, "whereclause", None)
    if wc is None:
        return out
    crits = getattr(wc, "clauses", None) or [wc]
    for crit in crits:
        name = getattr(getattr(crit, "left", None), "name", None)
        if name:
            out[name] = getattr(getattr(crit, "right", None), "value", None)
    return out


def _where_uuids(stmt) -> dict:
    vals = _where_vals(stmt)
    return {k: v for k, v in vals.items() if k in ("id", "task_id")}


@pytest.fixture()
def store():
    src = _source()
    return {
        "source": src,
        "tasks": [],
        "docs": [],
        "exports": [],
        "samples": [],
        "enqueued": [],
        "enqueue_error": None,
    }


@pytest.fixture()
def client(store, monkeypatch):
    session = FakeSession(store)

    async def fake_db():
        yield session

    async def fake_enqueue(task_id):
        if store["enqueue_error"]:
            raise store["enqueue_error"]
        store["enqueued"].append(str(task_id))

    monkeypatch.setattr(research_api, "_enqueue_research_task", fake_enqueue)

    app = FastAPI()
    app.include_router(research_api.router)
    app.dependency_overrides[get_db] = fake_db
    return TestClient(app)


class TestListSources:
    def test_returns_source_fields(self, client, store):
        r = client.get("/api/research/sources")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list) and len(data) == 1
        src = data[0]
        assert src["code"] == "qidian"
        assert src["verification_status"] == "experimental"
        assert src["enabled"] is True
        assert src["content_selector"] == "div.main"
        assert src["rate_limit"] == 1.0


class TestCreateTask:
    def test_invalid_source_id_format(self, client):
        r = client.post("/api/research/tasks", json={
            "source_id": "not-a-uuid",
            "target_url": "https://example.com",
        })
        assert r.status_code == 400

    def test_unknown_source_404(self, client, store):
        store["source"] = _source()
        r = client.post("/api/research/tasks", json={
            "source_id": str(uuid.uuid4()),
            "target_url": "https://example.com",
        })
        assert r.status_code == 404

    def test_disabled_source_409(self, client, store):
        store["source"] = _source(enabled=False)
        r = client.post("/api/research/tasks", json={
            "source_id": str(store["source"].id),
            "target_url": "https://example.com",
        })
        assert r.status_code == 409

    def test_relative_url_422(self, client, store):
        r = client.post("/api/research/tasks", json={
            "source_id": str(store["source"].id),
            "target_url": "not a url",
        })
        assert r.status_code == 422

    def test_happy_path_enqueues_and_returns_queued(self, client, store):
        r = client.post("/api/research/tasks", json={
            "source_id": str(store["source"].id),
            "target_url": "https://example.com/book/1",
        })
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "queued"
        assert data["source_code"] == "qidian"
        assert data["source_name"] == "起点中文网"
        assert data["progress"] == 0
        assert len(store["enqueued"]) == 1
        assert store["enqueued"][0] == data["id"]

    def test_enqueue_failure_marks_task_failed_503(self, client, store):
        store["enqueue_error"] = RuntimeError("redis down")
        r = client.post("/api/research/tasks", json={
            "source_id": str(store["source"].id),
            "target_url": "https://example.com/book/1",
        })
        assert r.status_code == 503
        task = store["tasks"][0]
        assert task.status == "failed"
        assert task.error_code == "ENQUEUE_FAILED"


class TestTaskListAndDetail:
    def test_list_returns_real_tasks_with_source(self, client, store):
        store["tasks"] = [_task(store["source"], status="completed", completed_count=3)]
        r = client.get("/api/research/tasks")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["status"] == "completed"
        assert data["tasks"][0]["source_code"] == "qidian"

    def test_detail_not_found_404(self, client, store):
        r = client.get(f"/api/research/tasks/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_detail_returns_task(self, client, store):
        t = _task(store["source"], status="running", progress=42)
        store["tasks"] = [t]
        r = client.get(f"/api/research/tasks/{t.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "running"
        assert data["progress"] == 42
        assert data["source_code"] == "qidian"


class TestCancel:
    def test_cancel_queued_finalizes_cancelled(self, client, store):
        t = _task(store["source"], status="queued")
        store["tasks"] = [t]
        r = client.post(f"/api/research/tasks/{t.id}/cancel")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"
        assert t.finished_at is not None

    def test_cancel_running_sets_cancel_requested(self, client, store):
        t = _task(store["source"], status="running")
        store["tasks"] = [t]
        r = client.post(f"/api/research/tasks/{t.id}/cancel")
        assert r.status_code == 200
        assert r.json()["status"] == "cancel_requested"

    def test_cancel_terminal_409(self, client, store):
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        r = client.post(f"/api/research/tasks/{t.id}/cancel")
        assert r.status_code == 409

    def test_delete_alias_cancels(self, client, store):
        t = _task(store["source"], status="running")
        store["tasks"] = [t]
        r = client.delete(f"/api/research/tasks/{t.id}")
        assert r.status_code == 200
        assert r.json()["status"] == "cancel_requested"


class TestDocuments:
    def test_list_task_documents(self, client, store):
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        store["docs"] = [_doc(t, i) for i in range(3)]
        r = client.get(f"/api/research/tasks/{t.id}/documents")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert len(data["documents"]) == 3
        assert data["documents"][0]["title"] == "第1章"
        assert "content" not in data["documents"][0]

    def test_document_detail_has_content(self, client, store):
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        d = _doc(t)
        store["docs"] = [d]
        r = client.get(f"/api/research/documents/{d.id}")
        assert r.status_code == 200
        data = r.json()
        assert data["content"] and len(data["content"]) >= 400
        assert data["char_count"] == len(data["content"])

    def test_document_not_found_404(self, client, store):
        r = client.get(f"/api/research/documents/{uuid.uuid4()}")
        assert r.status_code == 404

    def test_invalid_document_id_400(self, client):
        r = client.get("/api/research/documents/xyz")
        assert r.status_code == 400


class TestExport:
    def test_export_requires_completed_task(self, client, store):
        t = _task(store["source"], status="running")
        store["tasks"] = [t]
        r = client.post(f"/api/research/tasks/{t.id}/exports", json={"format": "txt"})
        assert r.status_code == 409

    def test_export_unsupported_format_422(self, client, store):
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        r = client.post(f"/api/research/tasks/{t.id}/exports", json={"format": "epub"})
        assert r.status_code == 422

    def test_export_no_documents_409(self, client, store):
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        r = client.post(f"/api/research/tasks/{t.id}/exports", json={"format": "txt"})
        assert r.status_code == 409

    def test_export_creates_real_file_and_row(self, client, store, tmp_path, monkeypatch):
        monkeypatch.setenv("RESEARCH_EXPORT_ROOT", str(tmp_path))
        t = _task(store["source"], status="completed", completed_count=2)
        store["tasks"] = [t]
        store["docs"] = [_doc(t, i) for i in range(2)]

        r = client.post(f"/api/research/tasks/{t.id}/exports", json={"format": "txt"})
        assert r.status_code == 201
        data = r.json()
        assert data["format"] == "txt"
        assert data["document_count"] == 2
        assert data["byte_size"] > 0
        assert data["download_url"] == f"/api/research/exports/{data['id']}/download"

        path = tmp_path / str(t.id) / f"{data['id']}.txt"
        assert path.is_file()
        assert path.stat().st_size == data["byte_size"]
        import hashlib as _h

        assert _h.sha256(path.read_bytes()).hexdigest() == data["content_hash"]
        text = path.read_text(encoding="utf-8")
        for d in store["docs"]:
            assert d.content in text

    def test_download_streams_file(self, client, store, tmp_path, monkeypatch):
        monkeypatch.setenv("RESEARCH_EXPORT_ROOT", str(tmp_path))
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        store["docs"] = [_doc(t, 0)]

        created = client.post(f"/api/research/tasks/{t.id}/exports", json={"format": "txt"})
        export_id = created.json()["id"]

        r = client.get(f"/api/research/exports/{export_id}/download")
        assert r.status_code == 200
        assert store["docs"][0].content in r.text

    def test_download_missing_file_404(self, client, store, tmp_path, monkeypatch):
        monkeypatch.setenv("RESEARCH_EXPORT_ROOT", str(tmp_path))
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        store["docs"] = [_doc(t, 0)]

        created = client.post(f"/api/research/tasks/{t.id}/exports", json={"format": "txt"})
        export_id = created.json()["id"]
        # simulate file loss on disk
        path = tmp_path / str(t.id) / f"{export_id}.txt"
        path.unlink()

        r = client.get(f"/api/research/exports/{export_id}/download")
        assert r.status_code == 404

    def test_get_export_metadata(self, client, store, tmp_path, monkeypatch):
        monkeypatch.setenv("RESEARCH_EXPORT_ROOT", str(tmp_path))
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        store["docs"] = [_doc(t, 0)]

        created = client.post(f"/api/research/tasks/{t.id}/exports", json={"format": "txt"})
        export_id = created.json()["id"]
        r = client.get(f"/api/research/exports/{export_id}")
        assert r.status_code == 200
        assert r.json()["task_id"] == str(t.id)


class TestImportReference:
    def test_import_all_creates_samples(self, client, store, tmp_path, monkeypatch):
        monkeypatch.setenv("REFERENCE_STORAGE_ROOT", str(tmp_path / "refs"))
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        store["docs"] = [_doc(t, i) for i in range(3)]
        book_id = str(uuid.uuid4())

        r = client.post(
            f"/api/research/tasks/{t.id}/import-reference",
            json={"book_id": book_id, "mode": "all"},
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["sample_ids"]) == 3
        assert data["created"] == 3
        assert data["deduped"] == 0

        # real files, DeepStudy-readable gzip text
        import gzip as _g

        for sample in store["samples"]:
            p = tmp_path / "refs" / book_id / f"{sample.id}.txt.gz"
            assert p.is_file()
            text = _g.decompress(p.read_bytes()).decode("utf-8")
            assert text in [d.content for d in store["docs"]]
            assert sample.status == "ready"
            assert sample.created_by == "research"

    def test_reimport_dedupes(self, client, store, tmp_path, monkeypatch):
        monkeypatch.setenv("REFERENCE_STORAGE_ROOT", str(tmp_path / "refs"))
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        store["docs"] = [_doc(t, 0)]
        book_id = str(uuid.uuid4())

        first = client.post(
            f"/api/research/tasks/{t.id}/import-reference",
            json={"book_id": book_id, "mode": "all"},
        ).json()
        second = client.post(
            f"/api/research/tasks/{t.id}/import-reference",
            json={"book_id": book_id, "mode": "all"},
        ).json()

        assert first["created"] == 1
        assert second["created"] == 0
        assert second["deduped"] == 1
        assert first["sample_ids"] == second["sample_ids"]

    def test_import_selected_documents(self, client, store, tmp_path, monkeypatch):
        monkeypatch.setenv("REFERENCE_STORAGE_ROOT", str(tmp_path / "refs"))
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        docs = [_doc(t, i) for i in range(3)]
        store["docs"] = docs
        book_id = str(uuid.uuid4())

        r = client.post(
            f"/api/research/tasks/{t.id}/import-reference",
            json={
                "book_id": book_id,
                "mode": "selected",
                "document_ids": [str(docs[1].id)],
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["sample_ids"]) == 1

    def test_import_selected_requires_ids_422(self, client, store):
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        store["docs"] = [_doc(t, 0)]
        r = client.post(
            f"/api/research/tasks/{t.id}/import-reference",
            json={"book_id": str(uuid.uuid4()), "mode": "selected"},
        )
        assert r.status_code == 422

    def test_import_invalid_mode_422(self, client, store):
        t = _task(store["source"], status="completed")
        store["tasks"] = [t]
        store["docs"] = [_doc(t, 0)]
        r = client.post(
            f"/api/research/tasks/{t.id}/import-reference",
            json={"book_id": str(uuid.uuid4()), "mode": "weird"},
        )
        assert r.status_code == 422

    def test_import_requires_completed_task(self, client, store):
        t = _task(store["source"], status="running")
        store["tasks"] = [t]
        r = client.post(
            f"/api/research/tasks/{t.id}/import-reference",
            json={"book_id": str(uuid.uuid4()), "mode": "all"},
        )
        assert r.status_code == 409
