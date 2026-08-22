"""Research export: materialize real TXT files from task documents (spec §25).

Every export writes a real file to disk AND a research_exports row with
content hash and byte size. No placeholder paths.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import ResearchDocument, ResearchExport, ResearchTask

logger = logging.getLogger("novelforge.research.exporter")

DEFAULT_EXPORT_ROOT = "/data/research-exports"


def export_root() -> Path:
    root = Path(os.environ.get("RESEARCH_EXPORT_ROOT", DEFAULT_EXPORT_ROOT))
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_txt(task: ResearchTask, documents: list[ResearchDocument]) -> str:
    """Render task documents as one plain-text manuscript."""
    source_line = ""
    if documents and documents[0].metadata_json:
        code = documents[0].metadata_json.get("source_code")
        if code:
            source_line = f"来源: {code}\n"

    parts: list[str] = [
        f"调研任务导出\n任务ID: {task.id}\n{source_line}"
        f"章节数: {len(documents)}\n"
    ]
    for doc in documents:
        parts.append(f"\n\n{'=' * 40}\n{doc.title}\n来源: {doc.source_url}\n{'=' * 40}\n\n")
        parts.append(doc.content)
    return "".join(parts)


async def load_task_documents(
    db: AsyncSession, task_id: uuid.UUID
) -> list[ResearchDocument]:
    rows = (
        await db.execute(
            select(ResearchDocument)
            .where(ResearchDocument.task_id == task_id)
            .order_by(ResearchDocument.ordinal.asc())
        )
    ).scalars().all()
    return list(rows)


async def export_task_txt(
    db: AsyncSession,
    *,
    task: ResearchTask,
    documents: list[ResearchDocument] | None = None,
) -> ResearchExport:
    """Write a real TXT file for the task and record it in research_exports."""
    if documents is None:
        documents = await load_task_documents(db, task.id)
    if not documents:
        raise ValueError("EXPORT_EMPTY: task has no documents")

    text = build_txt(task, documents)
    content = text.encode("utf-8")
    content_hash = hashlib.sha256(content).hexdigest()

    export_id = uuid.uuid4()
    task_dir = export_root() / str(task.id)
    task_dir.mkdir(parents=True, exist_ok=True)
    file_path = task_dir / f"{export_id}.txt"
    file_path.write_bytes(content)

    export = ResearchExport(
        id=export_id,
        task_id=task.id,
        format="txt",
        file_path=str(file_path),
        content_hash=content_hash,
        byte_size=len(content),
    )
    db.add(export)
    await db.flush()

    logger.info(
        "research export written task=%s export=%s bytes=%s docs=%s",
        task.id, export_id, len(content), len(documents),
    )
    return export
