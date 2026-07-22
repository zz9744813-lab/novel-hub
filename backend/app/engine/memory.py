"""Five-layer memory system - §5 v7.3.
L0: Redis scene buffer (ephemeral)
L1: Chapter fact ledger (per chapter, after finalization)
L2: 10-chapter stage summary
L3: Volume summary
L4: Authoritative state snapshot (per chapter + human)
"""
import uuid
import hashlib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    MemoryL1ChapterLedger, MemoryL2StageSummary, MemoryL3VolumeSummary,
    MemoryL4StateSnapshot, StoryEvent, SceneSearchDocument,
)


# Idempotency keys per §5.3
def l1_idempotency_key(book_id, chapter_id, finalized_version):
    return f"l1:{book_id}:{chapter_id}:{finalized_version}"

def l2_idempotency_key(book_id, chap_start, chap_end, outline_version):
    return f"l2:{book_id}:{chap_start}-{chap_end}:{outline_version}"

def l3_idempotency_key(book_id, volume_no, outline_version):
    return f"l3:{book_id}:{volume_no}:{outline_version}"

def l4_idempotency_key(book_id, entity_type, entity_id, as_of_chapter, version):
    return f"l4:{book_id}:{entity_type}:{entity_id}:{as_of_chapter}:{version}"


def compute_source_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


async def commit_l4_with_events(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    as_of_chapter: int,
    events: list[dict],
    source_run_id: uuid.UUID,
) -> None:
    """§5.5: Finalization atomic transaction.
    
    Order: story_events -> L4 snapshot -> L1 ledger -> scene_search_documents -> commit
    All in one transaction. Any failure = full rollback.
    """
    # Step 4: Write story_events
    for evt in events:
        if evt.get("certainty") != "explicit":
            continue  # §5.5: only explicit events go to L4
        story_event = StoryEvent(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=chapter_id,
            scene_id=uuid.UUID(evt["scene_id"]) if evt.get("scene_id") else chapter_id,
            event_type=evt.get("entity_type", "unknown"),
            subject_entity_ids=evt.get("subject_entity_ids", []),
            object_entity_ids=evt.get("object_entity_ids", []),
            evidence_paragraph_keys=evt.get("evidence_paragraph_keys", []),
            evidence_excerpt=evt.get("evidence", ""),
            certainty="explicit",
            canon_status="canon",
            source_run_id=source_run_id,
            version=1,
        )
        db.add(story_event)

        # Step 5: Update L4 snapshot
        entity_id = uuid.UUID(evt["entity_id"]) if isinstance(evt.get("entity_id"), str) else evt.get("entity_id")
        if entity_id:
            snap = MemoryL4StateSnapshot(
                id=uuid.uuid4(),
                book_id=book_id,
                entity_type=evt.get("entity_type", "character"),
                entity_id=entity_id,
                as_of_chapter=as_of_chapter,
                state={"field": evt.get("field"), "value": evt.get("new_value")},
                version=1,
                source_run_id=source_run_id,
            )
            db.add(snap)

    # Step 6: Generate L1 chapter ledger
    l1 = MemoryL1ChapterLedger(
        id=uuid.uuid4(),
        book_id=book_id,
        chapter_id=chapter_id,
        finalized_version=1,
        source_hash=compute_source_hash(str(events)),
        status="generated",
        ledger_json={"events": events},
        source_run_id=source_run_id,
    )
    db.add(l1)

    # Step 7: Generate scene_search_documents (done separately per scene)
    # Step 8: commit (handled by caller)


async def get_l4_state(db: AsyncSession, book_id: uuid.UUID,
                       entity_id: uuid.UUID, as_of_chapter: int) -> dict | None:
    """Get latest L4 state for an entity up to a chapter."""
    result = await db.execute(
        select(MemoryL4StateSnapshot).where(
            MemoryL4StateSnapshot.book_id == book_id,
            MemoryL4StateSnapshot.entity_id == entity_id,
            MemoryL4StateSnapshot.as_of_chapter <= as_of_chapter,
        ).order_by(MemoryL4StateSnapshot.as_of_chapter.desc(),
                   MemoryL4StateSnapshot.version.desc()).limit(1)
    )
    snap = result.scalar_one_or_none()
    if snap and snap.is_locked:
        return {"state": snap.state, "locked": True}
    return {"state": snap.state, "locked": False} if snap else None


async def generate_l2_summary(db: AsyncSession, book_id: uuid.UUID,
                                chapter_start: int, chapter_end: int,
                                outline_version: int,
                                source_run_id: uuid.UUID) -> MemoryL2StageSummary:
    """Generate L2 summary for chapters [start, end]."""
    # Collect L1s for this range
    # TODO: use MemoryCompiler with LLM to summarize
    summary = MemoryL2StageSummary(
        id=uuid.uuid4(), book_id=book_id,
        chapter_range_start=chapter_start, chapter_range_end=chapter_end,
        outline_version=outline_version,
        source_hash="pending",
        status="generated",
        summary_json={"note": "TODO: LLM-generated summary"},
        source_run_id=source_run_id,
    )
    db.add(summary)
    return summary


async def generate_l3_summary(db: AsyncSession, book_id: uuid.UUID,
                                volume_no: int, outline_version: int,
                                source_run_id: uuid.UUID) -> MemoryL3VolumeSummary:
    """Generate L3 volume summary."""
    summary = MemoryL3VolumeSummary(
        id=uuid.uuid4(), book_id=book_id,
        volume_no=volume_no, outline_version=outline_version,
        source_hash="pending",
        status="generated",
        summary_json={"note": "TODO: LLM-generated volume summary"},
        source_run_id=source_run_id,
    )
    db.add(summary)
    return summary
