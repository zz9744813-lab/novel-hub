"""Five-layer memory system - §5 v7.3.
L0: Redis scene buffer (ephemeral)
L1: Chapter fact ledger (per chapter, after finalization)
L2: 10-chapter stage summary
L3: Volume summary
L4: Authoritative state snapshot (per chapter + human)

Key fix: pg_advisory_xact_lock for L4 commits per §7.4.
C-25: Deterministic SHA-256 -> bigint advisory lock.
L4 updates MERGE previous state instead of replacing with single field.
"""
from __future__ import annotations

import copy
import uuid
import hashlib
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    MemoryL1ChapterLedger, MemoryL2StageSummary, MemoryL3VolumeSummary,
    MemoryL4StateSnapshot, StoryEvent,
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


async def _latest_l4_state(
    db: AsyncSession,
    book_id: uuid.UUID,
    entity_id: uuid.UUID,
    as_of_chapter: int,
) -> dict:
    """Load latest L4 state dict for merge (empty dict if none)."""
    result = await db.execute(
        select(MemoryL4StateSnapshot).where(
            MemoryL4StateSnapshot.book_id == book_id,
            MemoryL4StateSnapshot.entity_id == entity_id,
            MemoryL4StateSnapshot.as_of_chapter <= as_of_chapter,
        ).order_by(
            MemoryL4StateSnapshot.as_of_chapter.desc(),
            MemoryL4StateSnapshot.version.desc(),
        ).limit(1)
    )
    snap = result.scalar_one_or_none()
    if not snap or not snap.state:
        return {}
    # Normalize legacy {field,value} single-slot snapshots into a dict
    state = snap.state
    if isinstance(state, dict) and "field" in state and "value" in state and len(state) <= 3:
        return {state["field"]: state["value"]}
    return dict(state) if isinstance(state, dict) else {}


async def commit_l4_with_events(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    as_of_chapter: int,
    events: list[dict],
    source_run_id: uuid.UUID,
    finalized_version: int | None = None,
) -> None:
    """§5.5 + §7.4: Finalization atomic transaction with advisory lock.

    Order: advisory_lock -> story_events -> L4 snapshot (merge) -> L1 ledger
    Caller MUST commit the session after this returns successfully.
    """
    from app.v74_utils import advisory_lock_key
    lock_key = advisory_lock_key(book_id)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})

    # Resolve real finalized_version when available (B-06)
    if finalized_version is None:
        from app.models import Chapter

        ch = (
            await db.execute(select(Chapter).where(Chapter.id == chapter_id))
        ).scalar_one_or_none()
        if ch and ch.finalized_version is not None:
            finalized_version = int(ch.finalized_version)
        else:
            # Prefer max chapter_versions; never hardcode 1 forever
            from app.models import ChapterVersion
            from sqlalchemy import func as sa_func

            mv = (
                await db.execute(
                    select(sa_func.coalesce(sa_func.max(ChapterVersion.version), 0)).where(
                        ChapterVersion.chapter_id == chapter_id
                    )
                )
            ).scalar()
            finalized_version = int(mv or 0) or 1

    # Merge buffer: entity_id -> merged state
    merged: dict[uuid.UUID, dict] = {}
    entity_types: dict[uuid.UUID, str] = {}

    for evt in events:
        if evt.get("certainty") != "explicit":
            continue

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

        entity_id = evt.get("entity_id")
        if isinstance(entity_id, str):
            try:
                entity_id = uuid.UUID(entity_id)
            except ValueError:
                entity_id = None
        if not entity_id:
            continue

        if entity_id not in merged:
            merged[entity_id] = await _latest_l4_state(db, book_id, entity_id, as_of_chapter)
            entity_types[entity_id] = evt.get("entity_type", "character")

        field = evt.get("field")
        new_value = evt.get("new_value")
        if field is not None:
            merged[entity_id][field] = new_value
        elif isinstance(new_value, dict):
            merged[entity_id].update(new_value)

    # Write one L4 snapshot per entity with fully merged state
    for entity_id, state in merged.items():
        snap = MemoryL4StateSnapshot(
            id=uuid.uuid4(),
            book_id=book_id,
            entity_type=entity_types.get(entity_id, "character"),
            entity_id=entity_id,
            as_of_chapter=as_of_chapter,
            state=copy.deepcopy(state),
            version=1,
            source_run_id=source_run_id,
        )
        db.add(snap)

    # L1 chapter ledger
    l1 = MemoryL1ChapterLedger(
        id=uuid.uuid4(),
        book_id=book_id,
        chapter_id=chapter_id,
        finalized_version=int(finalized_version),
        source_hash=compute_source_hash(str(events)),
        status="generated",
        ledger_json={"events": events},
        source_run_id=source_run_id,
    )
    db.add(l1)


async def get_l4_state(
    db: AsyncSession,
    book_id: uuid.UUID,
    entity_id: uuid.UUID,
    as_of_chapter: int,
) -> dict | None:
    """Get latest L4 state for an entity up to a chapter."""
    result = await db.execute(
        select(MemoryL4StateSnapshot).where(
            MemoryL4StateSnapshot.book_id == book_id,
            MemoryL4StateSnapshot.entity_id == entity_id,
            MemoryL4StateSnapshot.as_of_chapter <= as_of_chapter,
        ).order_by(
            MemoryL4StateSnapshot.as_of_chapter.desc(),
            MemoryL4StateSnapshot.version.desc(),
        ).limit(1)
    )
    snap = result.scalar_one_or_none()
    if snap and snap.is_locked:
        return {"state": snap.state, "locked": True}
    return {"state": snap.state, "locked": False} if snap else None


