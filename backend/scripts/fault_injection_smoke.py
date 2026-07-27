"""Fault-injection style checks for AI__.md v3.0 §14.4 (no full LLM).

Runs inside API container against live Postgres.
"""
from __future__ import annotations

import asyncio
import uuid

from app.agents.patch_editor import apply_patches, compute_hash, PatchStaleError
from app.engine.chapter_finalizer import commit_final_chapter_snapshot, FinalScene
from app.engine.final_artifact import build_final_artifact
from app.engine.step_runner import acquire_run_lease, release_run_lease
from app.database import async_session_factory
from sqlalchemy import select, text, func
from app.models import Book, Chapter, ChapterVersion, ChapterRun, StoryEvent, Scene


async def main() -> None:
    results = []

    # FI: Patch hash mismatch → zero mutation
    original = "甲段落内容足够长。\n\n乙段落内容足够长。"
    try:
        await apply_patches(
            original,
            [{"expected_hash": "00" * 32, "replacement_text": "篡改", "paragraph_key": "p"}],
        )
        results.append("PATCH_STALE_FAIL")
    except PatchStaleError:
        results.append("PATCH_STALE_OK")

    # FI: Canon exception mid-finalize → no partial final (simulate bad evidence)
    async with async_session_factory() as db:
        row = (
            await db.execute(
                text(
                    """
                    SELECT b.id as book_id, n.id as node_id, ov.id as ov_id
                    FROM books b
                    JOIN outline_nodes n ON n.book_id = b.id
                    JOIN outline_versions ov ON ov.book_id = b.id
                    LIMIT 1
                    """
                )
            )
        ).mappings().first()
        if not row:
            results.append("NO_BOOK_SKIP")
            print(" ".join(results))
            return
        book_id, node_id, ov = row["book_id"], row["node_id"], row["ov_id"]
        max_no = (
            await db.execute(
                text("SELECT coalesce(max(chapter_no),0) FROM chapters WHERE book_id=:b"),
                {"b": book_id},
            )
        ).scalar()
        ch_no = int(max_no) + 1
        ch = Chapter(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_no=ch_no,
            outline_node_id=node_id,
            status="state_extracting",
            title="fi-test",
        )
        db.add(ch)
        await db.commit()
        chapter_id = ch.id

    art = build_final_artifact(
        [{"scene_no": 1, "content": "足够长的正文内容。" * 5, "summary": "s"}]
    )
    # intentional bad evidence hash → finalizer rejects candidates, no final
    bad_events = [
        {
            "event_key": "bad",
            "entity_type": "character",
            "entity_id": str(uuid.uuid4()),
            "field": "x",
            "new_value": "y",
            "certainty": "explicit",
            "scene_no": 1,
            "evidence_paragraph_key": "nope",
            "evidence_hash": "deadbeef",
            "evidence": "nope",
        }
    ]
    snap = await commit_final_chapter_snapshot(
        book_id=book_id,
        chapter_id=chapter_id,
        expected_previous_version=0,
        final_artifact=art,
        final_scenes=[FinalScene(scene_no=1, content=art.scenes[0].content, summary="s")],
        validated_events=bad_events,
        source_run_ids=[uuid.uuid4()],
        outline_node_id=node_id,
        outline_version_id=ov,
        title="fi-test",
        chapter_no=ch_no,
    )
    # Depending on implementation: may still finalize with empty events if it filters bad
    async with async_session_factory() as db:
        finals = (
            await db.execute(
                select(func.count())
                .select_from(ChapterVersion)
                .where(
                    ChapterVersion.chapter_id == chapter_id,
                    ChapterVersion.version_kind == "final",
                )
            )
        ).scalar()
        events = (
            await db.execute(
                select(func.count())
                .select_from(StoryEvent)
                .where(StoryEvent.chapter_id == chapter_id)
            )
        ).scalar()
    # Accept either: reject whole finalize OR finalize without bad canon events
    if not snap.ok:
        results.append("FINALIZE_REJECT_OK")
    elif finals == 1 and events == 0:
        results.append("FINALIZE_FILTER_BAD_EVENT_OK")
    else:
        results.append(f"FINALIZE_PARTIAL? ok={snap.ok} finals={finals} events={events}")

    # FI: lease no takeover
    async with async_session_factory() as db:
        ch2 = (await db.execute(select(Chapter).limit(1))).scalar_one_or_none()
        if not ch2:
            results.append("LEASE_SKIP_NO_CHAPTER")
            print(" ".join(results))
            return
        book = (await db.execute(select(Book).where(Book.id == ch2.book_id))).scalar_one()
        # clear stuck active runs for this chapter so INV-04 unique allows insert
        await db.execute(
            text(
                """
                UPDATE chapter_runs
                SET status = 'abandoned', finished_at = now()
                WHERE chapter_id = :c
                  AND status IN ('queued','running','paused','waiting_dependency','retryable')
                """
            ),
            {"c": ch2.id},
        )
        ch_no = getattr(ch2, "chapter_no", 1) or 1
        ov = (
            await db.execute(text("SELECT id FROM outline_versions WHERE book_id=:b LIMIT 1"), {"b": book.id})
        ).scalar()
        run = ChapterRun(
            id=uuid.uuid4(),
            book_id=book.id,
            chapter_id=ch2.id,
            chapter_no=ch_no,
            outline_version_id=ov,
            status="queued",
            pipeline_version="fi",
            request_id=f"fi-{uuid.uuid4().hex[:8]}",
            control_requested="none",
            budget_snapshot={},
            model_binding_snapshot={},
        )
        db.add(run)
        await db.commit()
        rid = run.id
    a = await acquire_run_lease(rid, "w1", lease_seconds=60)
    b = await acquire_run_lease(rid, "w2", lease_seconds=60)
    await release_run_lease(rid, "w1")
    results.append("LEASE_NO_TAKEOVER_OK" if a and not b else f"LEASE_FAIL a={bool(a)} b={bool(b)}")

    # INV SQL batch
    async with async_session_factory() as db:
        for name, sql in [
            (
                "FINAL_PTR",
                """
                SELECT count(*) FROM chapters c
                LEFT JOIN chapter_versions v
                  ON v.chapter_id = c.id AND v.version = c.finalized_version AND v.version_kind = 'final'
                WHERE c.status = 'finalized' AND (c.finalized_version IS NULL OR v.id IS NULL)
                """,
            ),
            (
                "ACTIVE_RUN",
                """
                SELECT count(*) FROM (
                  SELECT chapter_id FROM chapter_runs
                  WHERE status IN ('queued','running','paused','waiting_dependency','retryable')
                  GROUP BY chapter_id HAVING count(*) > 1
                ) t
                """,
            ),
        ]:
            n = (await db.execute(text(sql))).scalar()
            results.append(f"{name}_{'OK' if n == 0 else 'FAIL:'+str(n)}")

    print(" ".join(results))
    if any(x.endswith("FAIL") or x.startswith("FINALIZE_PARTIAL") or "FAIL:" in x for x in results):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
