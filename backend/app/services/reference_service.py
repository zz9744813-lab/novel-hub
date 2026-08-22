"""Reference library service (spec §26).

Single canonical path for creating ReferenceSample rows. Research and the
upload API share this logic; Research never invents a second reference
data structure.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import ReferenceSample

logger = logging.getLogger("novelforge.reference_service")

DEFAULT_REFERENCE_ROOT = "/data/references"


def reference_root() -> Path:
    return Path(os.environ.get("REFERENCE_STORAGE_ROOT", DEFAULT_REFERENCE_ROOT))


async def find_by_content_hash(
    db: AsyncSession, *, book_id: uuid.UUID, content_sha256: str
) -> ReferenceSample | None:
    return (
        await db.execute(
            select(ReferenceSample).where(
                ReferenceSample.book_id == book_id,
                ReferenceSample.content_sha256 == content_sha256,
            )
        )
    ).scalar_one_or_none()


async def create_reference_sample_from_text(
    db: AsyncSession,
    *,
    book_id: uuid.UUID,
    text: str,
    filename: str,
    genre_hint: str | None = None,
    created_by: str = "research",
    source_kind: str = "research",
    source_ref: dict | None = None,
) -> tuple[ReferenceSample, bool]:
    """Persist one reference sample (gzip on disk + DB row).

    Returns (sample, created). Idempotent on (book_id, content_sha256):
    re-importing the same text returns the existing sample with created=False.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("EMPTY_REFERENCE_TEXT")

    content_bytes = text.encode("utf-8")
    sha = hashlib.sha256(content_bytes).hexdigest()

    existing = await find_by_content_hash(db, book_id=book_id, content_sha256=sha)
    if existing is not None:
        return existing, False

    sample_id = uuid.uuid4()
    out_dir = reference_root() / str(book_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sample_id}.txt.gz"
    compressed = gzip.compress(content_bytes)
    out_path.write_bytes(compressed)

    sample = ReferenceSample(
        id=sample_id,
        book_id=book_id,
        original_filename=filename[:500],
        storage_path=str(out_path),
        content_sha256=sha,
        mime_type="text/plain",
        original_size_bytes=len(content_bytes),
        compressed_size_bytes=len(compressed),
        character_count=len(text),
        genre_hint=genre_hint,
        status="ready",
        created_by=created_by,
    )
    db.add(sample)
    await db.flush()
    logger.info(
        "reference sample created book=%s sample=%s chars=%s by=%s",
        book_id, sample_id, len(text), created_by,
    )
    return sample, True


async def create_reference_sample_from_archive(
    db: AsyncSession,
    *,
    book_id: uuid.UUID,
    file_path: str | Path,
    filename: str | None = None,
    genre_hint: str | None = None,
    source_kind: str = "research",
    source_ref: dict | None = None,
) -> tuple[ReferenceSample, bool]:
    """Create one combined sample from an archive of .txt chapters."""
    import zipfile

    path = Path(file_path)
    if not path.exists():
        raise ValueError(f"ARCHIVE_MISSING: {path}")

    chunks: list[str] = []
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = sorted(n for n in zf.namelist() if n.endswith(".txt"))
            if not names:
                raise ValueError("ARCHIVE_EMPTY: no .txt entries")
            for name in names:
                data = zf.read(name)
                chunks.append(data.decode("utf-8", errors="ignore").strip())
    else:
        raw = path.read_bytes()
        if path.suffix == ".gz":
            raw = gzip.decompress(raw)
        chunks.append(raw.decode("utf-8", errors="ignore").strip())

    text = "\n\n\n".join(c for c in chunks if c)
    if not text.strip():
        raise ValueError("ARCHIVE_EMPTY: no text content")

    return await create_reference_sample_from_text(
        db,
        book_id=book_id,
        text=text,
        filename=filename or path.name,
        genre_hint=genre_hint,
        created_by="research",
        source_kind=source_kind,
        source_ref=source_ref,
    )
