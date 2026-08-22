"""v9.1 PR-09: Export + Reference service tests (spec §25, §26).

Real files on disk, real rows in DB (fake session), hash consistency,
idempotent import, and DeepStudy-readable gzipped text samples.
"""
from __future__ import annotations

import gzip
import hashlib
import uuid
import zipfile
from pathlib import Path

import pytest

from app.models import ResearchDocument, ResearchSource, ResearchTask, ReferenceSample
from app.research.exporter import build_txt, export_task_txt


def _source() -> ResearchSource:
    return ResearchSource(
        id=uuid.uuid4(),
        code="qidian",
        name="起点中文网",
        base_url="https://www.qidian.com",
        content_selector="div.main",
        encoding="utf-8",
        rate_limit=1.0,
        enabled=True,
        verification_status="experimental",
        config_json={},
    )


def _task(source: ResearchSource) -> ResearchTask:
    return ResearchTask(
        id=uuid.uuid4(),
        book_id=None,
        source_id=source.id,
        target_url="https://example.com/book/1",
        status="completed",
        progress=100,
        discovered_count=2,
        completed_count=2,
        current_url=None,
        error_code=None,
        error_detail=None,
        started_at=None,
        finished_at=None,
    )


def _doc(task: ResearchTask, ordinal: int) -> ResearchDocument:
    content = f"第{ordinal + 1}章正文内容。" * 60
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


class ExportResult:
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


class ExportSession:
    """Minimal session: tracks added ResearchExport/ReferenceSample rows."""

    def __init__(self, documents: list[ResearchDocument]):
        self.documents = documents
        self.added: list = []

    async def execute(self, stmt):
        cols = getattr(stmt, "column_descriptions", [])
        entity = cols[0].get("entity") if cols else None
        if entity is ResearchDocument:
            return ExportResult(self.documents)
        if entity is ReferenceSample:
            for row in self.added:
                if isinstance(row, ReferenceSample):
                    # dedupe lookup by book+hash handled below
                    pass
            return ExportResult(None)
        return ExportResult(None)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        pass


class DedupeSession(ExportSession):
    """Returns first matching ReferenceSample by content hash."""

    def __init__(self, documents: list[ResearchDocument], samples: list[ReferenceSample]):
        super().__init__(documents)
        self.samples = samples

    async def execute(self, stmt):
        cols = getattr(stmt, "column_descriptions", [])
        entity = cols[0].get("entity") if cols else None
        if entity is ReferenceSample:
            vals = {}
            wc = getattr(stmt, "whereclause", None)
            crits = getattr(wc, "clauses", None) or ([wc] if wc else [])
            for crit in crits:
                name = getattr(getattr(crit, "left", None), "name", None)
                if name:
                    vals[name] = getattr(getattr(crit, "right", None), "value", None)
            for s in self.samples:
                if (
                    str(s.book_id) == str(vals.get("book_id"))
                    and s.content_sha256 == vals.get("content_sha256")
                ):
                    return ExportResult(s)
            return ExportResult(None)
        return await super().execute(stmt)


class TestBuildTxt:
    def test_contains_all_chapters_and_titles(self):
        src = _source()
        task = _task(src)
        docs = [_doc(task, 0), _doc(task, 1)]
        text = build_txt(task, docs)
        assert "第1章" in text and "第2章" in text
        assert docs[0].content in text and docs[1].content in text
        assert str(task.id) in text
        assert "qidian" in text

    def test_order_follows_ordinal(self):
        src = _source()
        task = _task(src)
        docs = [_doc(task, 0), _doc(task, 1)]
        text = build_txt(task, docs)
        assert text.index(docs[0].content) < text.index(docs[1].content)


class TestExportTaskTxt:
    def test_writes_real_file_and_row(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RESEARCH_EXPORT_ROOT", str(tmp_path))
        src = _source()
        task = _task(src)
        docs = [_doc(task, 0), _doc(task, 1)]
        session = ExportSession(docs)

        import asyncio

        export = asyncio.run(export_task_txt(session, task=task, documents=docs))

        # file exists, size > 0, content consistent
        path = Path(export.file_path)
        assert path.is_file()
        assert export.byte_size == path.stat().st_size > 0
        content = path.read_bytes()
        assert export.content_hash == hashlib.sha256(content).hexdigest()
        text = content.decode("utf-8")
        for d in docs:
            assert d.content in text

        # row recorded
        assert export in session.added
        assert export.task_id == task.id
        assert export.format == "txt"

    def test_empty_documents_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RESEARCH_EXPORT_ROOT", str(tmp_path))
        src = _source()
        task = _task(src)
        import asyncio

        with pytest.raises(ValueError):
            asyncio.run(export_task_txt(ExportSession([]), task=task, documents=[]))


class TestReferenceService:
    def test_from_text_creates_sample_and_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REFERENCE_STORAGE_ROOT", str(tmp_path))
        from app.services.reference_service import create_reference_sample_from_text

        book_id = uuid.uuid4()
        text = "参考资料正文。" * 100
        session = DedupeSession([], [])

        import asyncio

        sample, created = asyncio.run(
            create_reference_sample_from_text(
                session,
                book_id=book_id,
                text=text,
                filename="research_abc00001_0000.txt",
            )
        )

        assert created is True
        assert sample.status == "ready"
        assert sample.created_by == "research"
        assert sample.character_count == len(text)
        assert sample.original_size_bytes == len(text.encode("utf-8"))
        assert sample.content_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()

        # file is gzipped plain text — DeepStudy readable
        path = Path(sample.storage_path)
        assert path.is_file()
        assert gzip.decompress(path.read_bytes()).decode("utf-8") == text
        assert sample.compressed_size_bytes == path.stat().st_size

    def test_from_text_empty_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REFERENCE_STORAGE_ROOT", str(tmp_path))
        from app.services.reference_service import create_reference_sample_from_text

        import asyncio

        with pytest.raises(ValueError):
            asyncio.run(
                create_reference_sample_from_text(
                    DedupeSession([], []),
                    book_id=uuid.uuid4(),
                    text="  ",
                    filename="x.txt",
                )
            )

    def test_from_text_dedupes_same_content(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REFERENCE_STORAGE_ROOT", str(tmp_path))
        from app.services.reference_service import create_reference_sample_from_text

        book_id = uuid.uuid4()
        text = "重复导入的内容。" * 100

        import asyncio

        session = DedupeSession([], [])
        sample1, created1 = asyncio.run(
            create_reference_sample_from_text(
                session, book_id=book_id, text=text, filename="a.txt"
            )
        )
        session.samples.append(sample1)
        session.added.clear()

        sample2, created2 = asyncio.run(
            create_reference_sample_from_text(
                session, book_id=book_id, text=text, filename="a.txt"
            )
        )
        assert created1 is True and created2 is False
        assert sample1.id == sample2.id

    def test_from_archive_zip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REFERENCE_STORAGE_ROOT", str(tmp_path / "refs"))
        from app.services.reference_service import create_reference_sample_from_archive

        archive = tmp_path / "chapters.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("chapter_0001.txt", "第一章内容。" * 50)
            zf.writestr("chapter_0002.txt", "第二章内容。" * 50)

        book_id = uuid.uuid4()
        session = DedupeSession([], [])

        import asyncio

        sample, created = asyncio.run(
            create_reference_sample_from_archive(
                session,
                book_id=book_id,
                file_path=archive,
                source_kind="research",
                source_ref={"task_id": "t-1"},
            )
        )
        assert created is True
        text = gzip.decompress(Path(sample.storage_path).read_bytes()).decode("utf-8")
        assert "第一章内容。" in text and "第二章内容。" in text

    def test_from_archive_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REFERENCE_STORAGE_ROOT", str(tmp_path))
        from app.services.reference_service import create_reference_sample_from_archive

        import asyncio

        with pytest.raises(ValueError):
            asyncio.run(
                create_reference_sample_from_archive(
                    DedupeSession([], []),
                    book_id=uuid.uuid4(),
                    file_path=tmp_path / "nope.zip",
                )
            )
