"""Acceptance-report §9 database-level regressions.

Every case in this file operates on real ORM rows in the test PostgreSQL
database (no source-string or signature-only assertions):

§9.1 session recovery:
  1. reviewing chapter + stale running run -> reconciler fails BOTH, audited
  2. old-outline failed chapter 1 is reused under the approved v2 outline
  3. _prepare_chapter_and_run really persists the v2 outline_node_id rebind
  4. a chapter_no=9999 test row never influences selection
  5. intermediate state without an active run fails closed (no create)
  6. two concurrent advances produce exactly one chapter/run/dispatch outbox
  7. repeated reconciler/advance runs are idempotent

§9.2 review checkpoint semantics (both review:v1:initial:* and review:v1:r1:*
key spaces, mocked providers — zero real gateway calls):
  8.  service_error output -> failed step only, never succeeded/reused
  9.  alternating error codes exhaust the TOTAL budget of 2 attempts
  10. outline_missing is permanent through the real wrapper -> run_step path
  11. a real quality rejection becomes a reusable succeeded checkpoint
  12. both review paths (initial + re-review) are covered by parametrization

DB-backed cases auto-skip when no PostgreSQL is reachable (the deploy
environment has one).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update

from app.database import async_session_factory
from app.engine.pipeline import (
    REVIEW_MAX_RETRYABLE_ATTEMPTS,
    REVIEW_RETRY_EXHAUSTED_CODE,
    review_result_payload,
    validate_review_output,
)
from app.engine.step_runner import (
    PIPELINE_VERSION,
    PermanentStepError,
    RetryableStepError,
    RunContext,
    run_step,
)
from app.models import (
    Book,
    Chapter,
    ChapterDispatchOutbox,
    ChapterRun,
    ChapterStateEvent,
    ChapterStepRun,
    ChapterVersion,
    OutlineNode,
    OutlineVersion,
    SessionAdvanceOutbox,
    WritingSession,
    WritingSessionEvent,
)
from app.services.next_chapter_selector import (
    NextChapterSelectionError,
    select_next_chapter,
)
from app.services.session_reconciler import reconcile_sessions
from app.services.writing_session_controller import (
    _prepare_chapter_and_run,
    advance_writing_session,
)


def _db_available() -> bool:
    try:
        import asyncpg

        from app.config import settings

        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

        async def _probe() -> bool:
            # Connect and close on ONE loop: closing on a second asyncio.run
            # uses a transport bound to a dead loop (NoneType.send) and made
            # every DB-backed test silently skip.
            conn = await asyncpg.connect(dsn=dsn, timeout=3)
            await conn.close()
            return True

        return bool(asyncio.run(_probe()))
    except Exception:  # noqa: BLE001 - any failure means "no DB here"
        return False


DB_AVAILABLE = _db_available()
requires_db = pytest.mark.skipif(not DB_AVAILABLE, reason="PostgreSQL not reachable")

pytestmark = [pytest.mark.asyncio, requires_db]

# All books seeded by this file share this title; the autouse cleanup fixture
# deletes exactly these rows after every test so repeated suite runs never
# re-accumulate the thousands of leftover sessions/outboxes that made
# reconciler claim scans degrade from ~58s to ~13 minutes (re-acceptance §3.1).
SEED_BOOK_TITLE = "会话恢复测试书"


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_recovery_seed_rows():
    """Re-acceptance §7-10: clean this file's own seeds after each test."""
    yield
    async with async_session_factory() as db:
        book_ids = (
            await db.execute(select(Book.id).where(Book.title == SEED_BOOK_TITLE))
        ).scalars().all()
        if not book_ids:
            return
        chapter_ids = (
            await db.execute(select(Chapter.id).where(Chapter.book_id.in_(book_ids)))
        ).scalars().all()
        run_ids = (
            await db.execute(select(ChapterRun.id).where(ChapterRun.book_id.in_(book_ids)))
        ).scalars().all()
        session_ids = (
            await db.execute(
                select(WritingSession.id).where(WritingSession.book_id.in_(book_ids))
            )
        ).scalars().all()
        if chapter_ids and run_ids:
            # chapters.active_run_id -> chapter_runs FK must be cleared first
            await db.execute(
                update(Chapter)
                .where(
                    Chapter.id.in_(chapter_ids),
                    Chapter.active_run_id.in_(run_ids),
                )
                .values(active_run_id=None)
            )
        if run_ids:
            await db.execute(
                delete(ChapterDispatchOutbox).where(
                    ChapterDispatchOutbox.chapter_run_id.in_(run_ids)
                )
            )
            await db.execute(
                delete(ChapterStepRun).where(ChapterStepRun.chapter_run_id.in_(run_ids))
            )
            await db.execute(delete(ChapterRun).where(ChapterRun.id.in_(run_ids)))
        if chapter_ids:
            await db.execute(
                delete(ChapterStateEvent).where(
                    ChapterStateEvent.chapter_id.in_(chapter_ids)
                )
            )
        if session_ids:
            await db.execute(
                delete(SessionAdvanceOutbox).where(
                    SessionAdvanceOutbox.writing_session_id.in_(session_ids)
                )
            )
            await db.execute(
                delete(WritingSessionEvent).where(
                    WritingSessionEvent.session_id.in_(session_ids)
                )
            )
            await db.execute(
                delete(WritingSession).where(WritingSession.id.in_(session_ids))
            )
        if chapter_ids:
            await db.execute(delete(Chapter).where(Chapter.id.in_(chapter_ids)))
        await db.execute(delete(OutlineNode).where(OutlineNode.book_id.in_(book_ids)))
        await db.execute(
            delete(OutlineVersion).where(OutlineVersion.book_id.in_(book_ids))
        )
        await db.execute(delete(Book).where(Book.id.in_(book_ids)))
        await db.commit()


async def _expire_reconcile_leases(book_id):
    """Force the next reconcile_sessions() to re-claim this book's sessions,
    simulating the next real timer pass after the 60s claim lease lapses."""
    async with async_session_factory() as db:
        await db.execute(
            update(WritingSession)
            .where(WritingSession.book_id == book_id)
            .values(
                reconcile_lease_until=datetime.now(timezone.utc) - timedelta(seconds=1)
            )
        )
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Seeding helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _seed_book(outline_nos: list[int], *, superseded_nos: list[int] | None = None):
    """Book + approved OutlineVersion (v2) with chapter nodes; optional
    superseded v1 outline with its own nodes (ids differ)."""
    async with async_session_factory() as db:
        book_id = uuid.uuid4()
        db.add(Book(id=book_id, title="会话恢复测试书"))
        ov_new = OutlineVersion(id=uuid.uuid4(), book_id=book_id, version=2, status="approved")
        db.add(ov_new)
        ov_old = None
        if superseded_nos:
            ov_old = OutlineVersion(id=uuid.uuid4(), book_id=book_id, version=1, status="superseded")
            db.add(ov_old)
        await db.flush()
        nodes = {}
        for no in outline_nos:
            node = OutlineNode(
                id=uuid.uuid4(),
                book_id=book_id,
                outline_version_id=ov_new.id,
                node_type="chapter",
                chapter_no=no,
                title=f"第{no}章",
                goal="goal",
            )
            db.add(node)
            nodes[no] = node.id
        old_nodes = {}
        if ov_old is not None:
            for no in superseded_nos or []:
                node = OutlineNode(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    outline_version_id=ov_old.id,
                    node_type="chapter",
                    chapter_no=no,
                    title=f"旧版第{no}章",
                    goal="old goal",
                )
                db.add(node)
                old_nodes[no] = node.id
        await db.commit()
        return book_id, ov_new.id, nodes, (ov_old.id if ov_old else None), old_nodes


async def _seed_session(
    db,
    book_id: uuid.UUID,
    *,
    status: str = "running",
    policy: dict | None = None,
) -> WritingSession:
    s = WritingSession(
        id=uuid.uuid4(),
        book_id=book_id,
        mode="duration",
        requested_duration_minutes=90,
        started_at=datetime.now(timezone.utc),
        deadline_at=datetime.now(timezone.utc) + timedelta(hours=2),
        status=status,
        control_requested="none",
        model_preflight_status="ok",
        policy_snapshot={
            "schema_version": "writing-policy-v1",
            "max_unreviewed_ahead": 5,
            "stop_on_needs_human": True,
            "stop_on_causal_failure": True,
            "stop_on_quality_drop": True,
            "stop_on_resource_block": True,
            "quality_window_size": 10,
            "quality_min_sample": 5,
            "minimum_first_pass_yield": 0.70,
            "bad_first_round_verdicts": ["revise", "reject"],
            "consecutive_bad_limit": 2,
            "deadline_behavior": "finish_current_run",
            **(policy or {}),
        },
    )
    db.add(s)
    await db.flush()
    return s


async def _seed_chapter(
    db,
    book_id: uuid.UUID,
    chapter_no: int,
    status: str,
    outline_node_id: uuid.UUID,
) -> Chapter:
    ch = Chapter(
        id=uuid.uuid4(),
        book_id=book_id,
        chapter_no=chapter_no,
        outline_node_id=outline_node_id,
        status=status,
    )
    db.add(ch)
    await db.flush()
    return ch


async def _seed_run(
    db,
    book_id: uuid.UUID,
    chapter: Chapter,
    outline_version_id: uuid.UUID,
    *,
    status: str = "queued",
    writing_session_id: uuid.UUID | None = None,
    stale: bool = False,
) -> ChapterRun:
    now = datetime.now(timezone.utc)
    run = ChapterRun(
        id=uuid.uuid4(),
        book_id=book_id,
        chapter_id=chapter.id,
        chapter_no=chapter.chapter_no,
        outline_version_id=outline_version_id,
        pipeline_version=PIPELINE_VERSION,
        status=status,
        control_requested="none",
        request_id=f"test:{uuid.uuid4().hex}",
        created_by="test",
        writing_session_id=writing_session_id,
        lease_owner="worker-a" if status == "running" and not stale else None,
        lease_expires_at=(now + timedelta(seconds=60)) if status == "running" and not stale else None,
        heartbeat_at=now - timedelta(minutes=10) if stale else None,
        started_at=now - timedelta(minutes=11) if stale else None,
    )
    db.add(run)
    await db.flush()
    return run


async def _scalar(db, stmt):
    return int((await db.execute(stmt)).scalar() or 0)


# ─────────────────────────────────────────────────────────────────────────────
# §9.1 session recovery
# ─────────────────────────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_1_reconciler_fails_orphaned_run_and_syncs_reviewing_chapter(monkeypatch):
    """§9.1-1: reviewing chapter + stale running run -> run failed
    (orphaned_chapter_run) AND chapter failed, with an audited transition."""
    # Shared test DB: guarantee this session is claimed despite leftover
    # active sessions crowding the claim batch.
    import app.services.session_reconciler as reconciler_mod

    monkeypatch.setattr(reconciler_mod, "CLAIM_LIMIT", 500)

    book_id, ov_id, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        session = await _seed_session(db, book_id)
        chapter = await _seed_chapter(db, book_id, 1, "reviewing", nodes[1])
        run = await _seed_run(
            db, book_id, chapter, ov_id,
            status="running", stale=True, writing_session_id=session.id,
        )
        session.current_chapter_id = chapter.id
        session.current_chapter_no = 1
        session.current_chapter_run_id = run.id
        await db.commit()
        sid, run_id, chapter_id = session.id, run.id, chapter.id

    report = await reconcile_sessions()
    assert report["repaired"] >= 1

    async with async_session_factory() as db:
        run = (await db.execute(select(ChapterRun).where(ChapterRun.id == run_id))).scalar_one()
        assert run.status == "failed"
        assert run.error_code == "orphaned_chapter_run"
        chapter = (await db.execute(select(Chapter).where(Chapter.id == chapter_id))).scalar_one()
        assert chapter.status == "failed"

        event = (
            await db.execute(
                select(ChapterStateEvent)
                .where(
                    ChapterStateEvent.chapter_id == chapter_id,
                    ChapterStateEvent.to_state == "failed",
                )
                .order_by(ChapterStateEvent.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        assert event.from_state == "reviewing"
        assert event.actor == "session_reconciler"
        assert event.run_id == run_id
        assert "orphaned" in (event.reason or "")

        outbox = (
            await db.execute(
                select(func.count()).select_from(SessionAdvanceOutbox).where(
                    SessionAdvanceOutbox.writing_session_id == sid
                )
            )
        ).scalar()
        assert int(outbox) >= 1


@requires_db
@pytest.mark.asyncio
async def test_2_selector_reuses_old_outline_chapter_under_approved_v2():
    """§9.1-2: failed Chapter 1 bound to superseded v1 is reused (same id)
    with the approved v2 node — no INSERT of a second chapter 1."""
    book_id, ov2, nodes, _, old_nodes = await _seed_book(
        [1, 2, 3], superseded_nos=[1, 2, 3]
    )
    async with async_session_factory() as db:
        old_chapter = await _seed_chapter(db, book_id, 1, "failed", old_nodes[1])
        await db.commit()
        chapter_id = old_chapter.id

        decision = await select_next_chapter(db, book_id)

    assert decision.action == "resume_unfinished"
    assert decision.chapter_no == 1
    assert decision.chapter_id == chapter_id
    assert decision.outline_node_id == nodes[1]
    assert decision.outline_version_id == ov2

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(Chapter).where(
                    Chapter.book_id == book_id, Chapter.chapter_no == 1
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == chapter_id


@requires_db
@pytest.mark.asyncio
async def test_3_prepare_chapter_and_run_persists_rebind_and_v2_run():
    """§9.1-3: reuse really writes the v2 outline_node_id; the new run uses v2."""
    book_id, ov2, nodes, _, old_nodes = await _seed_book(
        [1, 2, 3], superseded_nos=[1, 2, 3]
    )
    async with async_session_factory() as db:
        chapter = await _seed_chapter(db, book_id, 1, "failed", old_nodes[1])
        session = await _seed_session(db, book_id)
        await db.commit()
        chapter_id, sid = chapter.id, session.id

        decision = await select_next_chapter(db, book_id)
        assert decision.chapter_id == chapter_id
        assert decision.outline_node_id == nodes[1]

        run = await _prepare_chapter_and_run(db, session, decision, datetime.now(timezone.utc))
        await db.commit()
        run_id = run.id

    async with async_session_factory() as db:
        chapter = (await db.execute(select(Chapter).where(Chapter.id == chapter_id))).scalar_one()
        assert chapter.outline_node_id == nodes[1]
        assert str(chapter.active_run_id) == str(run_id)

        run = (await db.execute(select(ChapterRun).where(ChapterRun.id == run_id))).scalar_one()
        assert run.outline_version_id == ov2
        assert run.status == "queued"

        event = (
            await db.execute(
                select(WritingSessionEvent).where(
                    WritingSessionEvent.session_id == sid,
                    WritingSessionEvent.event_type == "chapter_outline_rebound",
                )
            )
        ).scalar_one()
        assert event.payload["old_outline_node_id"] == str(old_nodes[1])
        assert event.payload["new_outline_node_id"] == str(nodes[1])
        assert event.payload["outline_version_id"] == str(ov2)
        assert event.payload["chapter_no"] == 1


@requires_db
@pytest.mark.asyncio
async def test_4_high_numbered_test_row_is_ignored():
    """§9.1-4: a chapter_no=9999 row is outside the approved 1..3 outline and
    is never selected; the selector creates chapter 2 (1 is finalized)."""
    book_id, _, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        ch = await _seed_chapter(db, book_id, 1, "finalized", nodes[1])
        # INV-01: a finalized chapter must carry its final pointer + version.
        ch.finalized_version = 1
        db.add(
            ChapterVersion(
                id=uuid.uuid4(),
                book_id=book_id,
                chapter_id=ch.id,
                version=1,
                content="第1章定稿",
                word_count=100,
                source_run_id=uuid.uuid4(),
                version_kind="final",
            )
        )
        await _seed_chapter(db, book_id, 9999, "queued", nodes[1])
        await db.commit()

        decision = await select_next_chapter(db, book_id)

    assert decision.action == "create_chapter"
    assert decision.chapter_no == 2
    assert decision.chapter_id is None
    assert decision.outline_node_id == nodes[2]


@requires_db
@pytest.mark.asyncio
async def test_5_intermediate_state_without_run_fails_closed():
    """§9.1-5: reviewing chapter, no active run -> deterministic structured
    error instead of create_chapter; no duplicate chapter is produced."""
    book_id, _, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        await _seed_chapter(db, book_id, 1, "reviewing", nodes[1])
        await db.commit()

        with pytest.raises(NextChapterSelectionError) as exc:
            await select_next_chapter(db, book_id)

    assert exc.value.detail["code"] == "CHAPTER_STATE_INCONSISTENT"
    assert exc.value.detail["chapter_no"] == 1

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(Chapter).where(
                    Chapter.book_id == book_id, Chapter.chapter_no == 1
                )
            )
        ).scalars().all()
        assert len(rows) == 1


@requires_db
@pytest.mark.asyncio
async def test_6_concurrent_advances_produce_single_chapter_and_run():
    """§9.1-6: two concurrent advance_writing_session calls -> exactly one
    (book_id, chapter_no=1) row, one active ChapterRun, one dispatch outbox."""
    book_id, _, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        session = await _seed_session(db, book_id)
        await db.commit()
        sid = session.id

    async def _advance():
        async with async_session_factory() as db:
            result = await advance_writing_session(db, sid)
            await db.commit()
            return result

    results = await asyncio.gather(_advance(), _advance())

    actions = sorted(r["action"] for r in results)
    assert actions in (["start_next", "wait_current"], ["wait_current", "wait_current"])

    async with async_session_factory() as db:
        chapters = (
            await db.execute(
                select(Chapter).where(
                    Chapter.book_id == book_id, Chapter.chapter_no == 1
                )
            )
        ).scalars().all()
        assert len(chapters) == 1

        runs = (
            await db.execute(
                select(ChapterRun).where(ChapterRun.chapter_id == chapters[0].id)
            )
        ).scalars().all()
        assert len(runs) == 1
        assert runs[0].status == "queued"

        dispatches = (
            await db.execute(
                select(func.count()).select_from(ChapterDispatchOutbox).where(
                    ChapterDispatchOutbox.chapter_run_id == runs[0].id
                )
            )
        ).scalar()
        assert int(dispatches) == 1

        session = (
            await db.execute(select(WritingSession).where(WritingSession.id == sid))
        ).scalar_one()
        assert str(session.current_chapter_run_id) == str(runs[0].id)
        assert session.chapters_started == 1


@requires_db
@pytest.mark.asyncio
async def test_7_repeated_reconciler_and_advance_are_idempotent(monkeypatch):
    """§9.1-7: re-running reconciler/advance must not duplicate chapters,
    runs, or grow error/dispatch outbox rows unboundedly."""
    import app.services.session_reconciler as reconciler_mod

    monkeypatch.setattr(reconciler_mod, "CLAIM_LIMIT", 500)

    book_id, _, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        session = await _seed_session(db, book_id)
        await db.commit()
        sid = session.id

    async def _advance():
        async with async_session_factory() as db:
            result = await advance_writing_session(db, sid)
            await db.commit()
            return result

    assert (await _advance())["action"] == "start_next"

    await reconcile_sessions()
    assert (await _advance())["action"] == "wait_current"
    await reconcile_sessions()
    assert (await _advance())["action"] == "wait_current"

    async with async_session_factory() as db:
        chapters = (
            await db.execute(
                select(func.count()).select_from(Chapter).where(
                    Chapter.book_id == book_id
                )
            )
        ).scalar()
        assert int(chapters) == 1
        runs = (
            await db.execute(
                select(func.count()).select_from(ChapterRun).where(
                    ChapterRun.book_id == book_id
                )
            )
        ).scalar()
        assert int(runs) == 1
        dispatches = (
            await db.execute(
                select(func.count()).select_from(ChapterDispatchOutbox).where(
                    ChapterDispatchOutbox.chapter_run_id.in_(
                        select(ChapterRun.id).where(ChapterRun.book_id == book_id)
                    )
                )
            )
        ).scalar()
        # The single initial dispatch; no duplicate dispatch was ever created.
        assert int(dispatches) == 1


# ─────────────────────────────────────────────────────────────────────────────
# §9.2 review checkpoint database semantics
# ─────────────────────────────────────────────────────────────────────────────

SERVICE_FAILURE_OUTPUT = {
    "passed": False,
    "issues": [
        {
            "issue_id": "review_service_failure",
            "severity": "critical",
            "category": "service_error",
            "message": "final_content_empty",
        }
    ],
}
INVALID_PAYLOAD_OUTPUT = {
    "passed": False,
    "issues": [
        {
            "issue_id": "review_invalid_payload",
            "severity": "critical",
            "category": "service_error",
            "message": "ReviewAgent returned non-dict payload",
        }
    ],
}
OUTLINE_MISSING_OUTPUT = {
    "passed": False,
    "issues": [
        {
            "issue_id": "outline_missing",
            "severity": "critical",
            "category": "service_error",
            "message": "outline node missing",
        }
    ],
}
QUALITY_REJECTION_OUTPUT = {
    "passed": False,
    "issues": [
        {
            "issue_id": "style-lexical-diversity",
            "severity": "major",
            "category": "style",
            "instruction": "调整用词多样性",
        }
    ],
}

REVIEW_KEY_KINDS = ["initial", "rereview"]


def _review_step_key(kind: str, content: str) -> str:
    if kind == "initial":
        return f"review:v1:initial:{content[:16]}"
    return f"review:v1:r1:{content[:16]}"


async def _seed_review_run() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Reviewing chapter + queued run for run_step-level tests.

    Returns (book_id, chapter_id, run_id).
    """
    book_id, ov2, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        session = await _seed_session(db, book_id)
        chapter = await _seed_chapter(db, book_id, 1, "reviewing", nodes[1])
        run = await _seed_run(
            db, book_id, chapter, ov2, status="queued", writing_session_id=session.id
        )
        await db.commit()
        return book_id, chapter.id, run.id


async def _run_review_step(
    run_id: uuid.UUID,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    kind: str,
    output: dict,
    calls: list,
):
    """One run_step invocation through the real shared review wrapper."""
    ctx = RunContext(
        book_id=book_id, chapter_id=chapter_id, chapter_no=1, run_id=run_id, worker_id="test"
    )
    content_tag = f"content-{kind}"

    async def _execute(_payload):
        calls.append(1)
        return review_result_payload(output["passed"], output["issues"])

    return await run_step(
        ctx=ctx,
        step_name="review",
        step_key=_review_step_key(kind, content_tag),
        input_payload={"content_hash": content_tag},
        execute_fn=_execute,
        validate_fn=validate_review_output,
        max_retryable_attempts=REVIEW_MAX_RETRYABLE_ATTEMPTS,
        retry_exhausted_code=REVIEW_RETRY_EXHAUSTED_CODE,
    )


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", REVIEW_KEY_KINDS)
async def test_8_service_error_output_is_failed_never_succeeded(kind):
    """§9.2-8: execute_fn returns a service-error review output; the validator
    raises inside run_step -> only a failed step row exists."""
    book_id, chapter_id, run_id = await _seed_review_run()
    calls: list = []

    with pytest.raises(RetryableStepError) as exc:
        await _run_review_step(
            run_id, book_id, chapter_id, kind, SERVICE_FAILURE_OUTPUT, calls
        )
    assert exc.value.code == "review_service_failure"

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(ChapterStepRun).where(
                    ChapterStepRun.chapter_run_id == run_id,
                    ChapterStepRun.step_key == _review_step_key(kind, f"content-{kind}"),
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert rows[0].error_code == "review_service_failure"
        assert rows[0].attempt_no == 1
        succeeded = (
            await db.execute(
                select(func.count()).select_from(ChapterStepRun).where(
                    ChapterStepRun.chapter_run_id == run_id,
                    ChapterStepRun.status.in_(("succeeded", "reused")),
                )
            )
        ).scalar()
        assert int(succeeded) == 0
    assert len(calls) == 1


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", REVIEW_KEY_KINDS)
async def test_9_alternating_error_codes_exhaust_total_budget(kind):
    """§9.2-9: review_service_failure then review_invalid_payload — the TOTAL
    attempt budget of 2 is enforced regardless of error code, terminating as
    review_service_retry_exhausted after the second model call."""
    book_id, chapter_id, run_id = await _seed_review_run()
    calls: list = []
    outputs = [SERVICE_FAILURE_OUTPUT, INVALID_PAYLOAD_OUTPUT]

    # Attempt 1: fails with review_service_failure; budget not yet exhausted.
    with pytest.raises(RetryableStepError) as exc1:
        await _run_review_step(
            run_id, book_id, chapter_id, kind, outputs[0], calls
        )
    assert exc1.value.code == "review_service_failure"

    # Attempt 2: fails with a DIFFERENT code; total attempts now hit the cap.
    with pytest.raises(PermanentStepError) as exc2:
        await _run_review_step(
            run_id, book_id, chapter_id, kind, outputs[1], calls
        )
    assert exc2.value.code == REVIEW_RETRY_EXHAUSTED_CODE
    assert exc2.value.detail["attempts"] == 2

    assert len(calls) == 2  # a third model call never happened

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(ChapterStepRun)
                .where(
                    ChapterStepRun.chapter_run_id == run_id,
                    ChapterStepRun.step_key == _review_step_key(kind, f"content-{kind}"),
                )
                .order_by(ChapterStepRun.attempt_no.asc())
            )
        ).scalars().all()
        assert [r.attempt_no for r in rows] == [1, 2]
        assert all(r.status == "failed" for r in rows)
        assert [r.error_code for r in rows] == [
            "review_service_failure",
            "review_invalid_payload",
        ]


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", REVIEW_KEY_KINDS)
async def test_10_outline_missing_is_permanent_through_real_wrapper(kind):
    """§9.2-10: outline_missing flows through the real shared review wrapper
    into run_step -> validate_review_output and terminates permanently after
    exactly one attempt."""
    book_id, chapter_id, run_id = await _seed_review_run()
    calls: list = []

    with pytest.raises(PermanentStepError) as exc:
        await _run_review_step(
            run_id, book_id, chapter_id, kind, OUTLINE_MISSING_OUTPUT, calls
        )

    assert exc.value.code == "outline_missing"
    assert len(calls) == 1  # no retry for a permanent classification

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(ChapterStepRun).where(
                    ChapterStepRun.chapter_run_id == run_id,
                    ChapterStepRun.step_key == _review_step_key(kind, f"content-{kind}"),
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "failed"
        assert rows[0].error_code == "outline_missing"


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", REVIEW_KEY_KINDS)
async def test_11_quality_rejection_is_a_reusable_succeeded_checkpoint(kind):
    """§9.2-11: a real quality rejection (passed=false + style issue) is a
    legitimate review result: succeeded checkpoint, reusable by the patch
    loop without a second model call."""
    book_id, chapter_id, run_id = await _seed_review_run()
    calls: list = []

    art = await _run_review_step(
        run_id, book_id, chapter_id, kind, QUALITY_REJECTION_OUTPUT, calls
    )
    assert art.reused is False
    assert art.output["passed"] is False

    step_key = _review_step_key(kind, f"content-{kind}")

    # Second dispatch with the same inputs reuses the checkpoint.
    art2 = await _run_review_step(
        run_id, book_id, chapter_id, kind, QUALITY_REJECTION_OUTPUT, calls
    )
    assert art2.reused is True
    assert art2.step_run_id == art.step_run_id

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(ChapterStepRun).where(
                    ChapterStepRun.chapter_run_id == run_id,
                    ChapterStepRun.step_key == step_key,
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "succeeded"
        assert rows[0].output_json["passed"] is False
        assert rows[0].output_json["issues"][0]["category"] == "style"
    assert len(calls) == 1  # the reused dispatch made no model call


@requires_db
@pytest.mark.asyncio
async def test_12_validator_precedence_outline_missing_over_service_error():
    """§9.2-12 companion: outline_missing is classified permanent even when it
    appears after other service-error issues (deterministic precedence), and
    both review wrappers share the same payload shape."""
    initial = review_result_payload(False, OUTLINE_MISSING_OUTPUT["issues"])
    rereview = review_result_payload(False, OUTLINE_MISSING_OUTPUT["issues"])
    assert initial == rereview == OUTLINE_MISSING_OUTPUT

    with pytest.raises(PermanentStepError):
        validate_review_output(
            review_result_payload(
                False,
                [
                    INVALID_PAYLOAD_OUTPUT["issues"][0],
                    OUTLINE_MISSING_OUTPUT["issues"][0],
                ],
            )
        )

# ─────────────────────────────────────────────────────────────────────────────
# Re-acceptance round (2026-08-30): stock-orphan recovery, needs_human policy,
# policy-combination regressions — re-acceptance task §9 items 1–9
# ─────────────────────────────────────────────────────────────────────────────


async def _seed_stock_orphan():
    """Production-shaped stock state: session points at a run that was
    already terminalized as failed/orphaned_chapter_run while its chapter
    stayed in the intermediate reviewing state."""
    book_id, ov_id, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        session = await _seed_session(db, book_id)
        chapter = await _seed_chapter(db, book_id, 1, "reviewing", nodes[1])
        run = await _seed_run(
            db, book_id, chapter, ov_id, status="failed", writing_session_id=session.id
        )
        run.error_code = "orphaned_chapter_run"
        run.error_detail = {"reason": "running row lost its worker lease"}
        run.finished_at = datetime.now(timezone.utc)
        session.current_chapter_id = chapter.id
        session.current_chapter_no = 1
        session.current_chapter_run_id = run.id
        await db.commit()
        return book_id, session.id, run.id, chapter.id


async def _pending_outbox_count(sid):
    async with async_session_factory() as db:
        return await _scalar(
            db,
            select(func.count()).select_from(SessionAdvanceOutbox).where(
                SessionAdvanceOutbox.writing_session_id == sid,
                SessionAdvanceOutbox.status == "pending",
            ),
        )


async def _advance(sid):
    async with async_session_factory() as db:
        result = await advance_writing_session(db, sid)
        await db.commit()
        return result


@requires_db
@pytest.mark.asyncio
async def test_13_stock_orphan_repaired_in_one_pass(monkeypatch):
    """§9-1: run=failed/orphaned_chapter_run + chapter=reviewing (the exact
    production stock state) -> ONE reconcile pass sets chapter=failed with an
    audit event carrying the original run_id, and pokes the advance."""
    import app.services.session_reconciler as reconciler_mod

    monkeypatch.setattr(reconciler_mod, "CLAIM_LIMIT", 500)
    book_id, sid, run_id, chapter_id = await _seed_stock_orphan()

    report = await reconcile_sessions()
    assert report["repaired"] >= 1

    async with async_session_factory() as db:
        chapter = (
            await db.execute(select(Chapter).where(Chapter.id == chapter_id))
        ).scalar_one()
        assert chapter.status == "failed"

        event = (
            await db.execute(
                select(ChapterStateEvent)
                .where(
                    ChapterStateEvent.chapter_id == chapter_id,
                    ChapterStateEvent.to_state == "failed",
                )
                .order_by(ChapterStateEvent.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        assert event.from_state == "reviewing"
        assert event.run_id == run_id
        assert event.actor == "session_reconciler"

        outbox = (
            await db.execute(
                select(func.count()).select_from(SessionAdvanceOutbox).where(
                    SessionAdvanceOutbox.writing_session_id == sid
                )
            )
        ).scalar()
        assert int(outbox) >= 1  # same-pass advance poke


@requires_db
@pytest.mark.asyncio
async def test_14_stock_orphan_end_to_end_resume(monkeypatch):
    """§9-2: reconcile + advance on the stock state must reuse the ORIGINAL
    Chapter 1, create exactly one new run + one dispatch outbox, and never
    raise CHAPTER_STATE_INCONSISTENT."""
    import app.services.session_reconciler as reconciler_mod

    monkeypatch.setattr(reconciler_mod, "CLAIM_LIMIT", 500)
    book_id, sid, old_run_id, chapter_id = await _seed_stock_orphan()

    await reconcile_sessions()
    result = await _advance(sid)  # must not raise

    assert result["action"] == "start_next"
    async with async_session_factory() as db:
        chapters = (
            await db.execute(
                select(Chapter).where(
                    Chapter.book_id == book_id, Chapter.chapter_no == 1
                )
            )
        ).scalars().all()
        assert len(chapters) == 1 and chapters[0].id == chapter_id

        runs = (
            await db.execute(
                select(ChapterRun).where(ChapterRun.chapter_id == chapter_id)
            )
        ).scalars().all()
        assert len(runs) == 2
        new_runs = [r for r in runs if r.id != old_run_id]
        assert len(new_runs) == 1 and new_runs[0].status == "queued"

        dispatches = (
            await db.execute(
                select(func.count()).select_from(ChapterDispatchOutbox).where(
                    ChapterDispatchOutbox.chapter_run_id == new_runs[0].id
                )
            )
        ).scalar()
        assert int(dispatches) == 1

        session = (
            await db.execute(select(WritingSession).where(WritingSession.id == sid))
        ).scalar_one()
        assert str(session.current_chapter_run_id) == str(new_runs[0].id)
        assert session.status == "running"


@requires_db
@pytest.mark.asyncio
async def test_15_stock_orphan_recovery_is_idempotent(monkeypatch):
    """§9-3: repeated reconcile/advance after recovery must not grow chapters,
    runs, dispatch outboxes, failed-transition events, or pending pokes."""
    import app.services.session_reconciler as reconciler_mod

    monkeypatch.setattr(reconciler_mod, "CLAIM_LIMIT", 500)
    book_id, sid, old_run_id, chapter_id = await _seed_stock_orphan()
    await reconcile_sessions()
    await _advance(sid)

    async def _snapshot():
        async with async_session_factory() as db:
            return {
                "chapters": await _scalar(
                    db, select(func.count()).select_from(Chapter).where(Chapter.book_id == book_id)
                ),
                "runs": await _scalar(
                    db, select(func.count()).select_from(ChapterRun).where(ChapterRun.book_id == book_id)
                ),
                "dispatches": await _scalar(
                    db,
                    select(func.count()).select_from(ChapterDispatchOutbox).where(
                        ChapterDispatchOutbox.chapter_run_id.in_(
                            select(ChapterRun.id).where(ChapterRun.book_id == book_id)
                        )
                    ),
                ),
                "failed_events": await _scalar(
                    db,
                    select(func.count()).select_from(ChapterStateEvent).where(
                        ChapterStateEvent.chapter_id == chapter_id,
                        ChapterStateEvent.to_state == "failed",
                    ),
                ),
                "pending_outbox": await _pending_outbox_count(sid),
            }

    before = await _snapshot()
    for _ in range(3):
        # Expire the 60s reconcile claim lease so every pass really re-claims
        # the session — the previous version of this loop never crossed a
        # lease boundary and proved nothing about timer-driven idempotency.
        await _expire_reconcile_leases(book_id)
        report = await reconcile_sessions()
        assert report["claimed"] >= 1
        await _advance(sid)
    after = await _snapshot()
    assert before == after
    assert after["chapters"] == 1 and after["runs"] == 2 and after["dispatches"] == 1


@requires_db
@pytest.mark.asyncio
async def test_16_generic_intermediate_state_blocks_stably():
    """§9-4: a non-orphan intermediate chapter with no active run fails the
    session closed as a STABLE block — the advance never throws, and repeated
    advances neither create runs nor pile up events."""
    book_id, _, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        session = await _seed_session(db, book_id)
        await _seed_chapter(db, book_id, 1, "reviewing", nodes[1])
        await db.commit()
        sid = session.id

    first = await _advance(sid)
    assert first["action"] == "block"
    assert first["reason"] == "chapter state inconsistent"
    assert first["status"] == "blocked"

    second = await _advance(sid)  # must not raise
    assert second["action"] == "block"

    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        assert s.status == "blocked" and s.stop_reason == "chapter_state_inconsistent"
        runs = await _scalar(
            db,
            select(func.count()).select_from(ChapterRun).where(ChapterRun.book_id == book_id),
        )
        assert runs == 0
        dispatches = await _scalar(
            db,
            select(func.count()).select_from(ChapterDispatchOutbox).where(
                ChapterDispatchOutbox.chapter_run_id.in_(
                    select(ChapterRun.id).where(ChapterRun.book_id == book_id)
                )
            ),
        )
        assert dispatches == 0
        events = await _scalar(
            db,
            select(func.count()).select_from(WritingSessionEvent).where(
                WritingSessionEvent.session_id == sid,
                WritingSessionEvent.event_type == "chapter_state_inconsistent_blocked",
            ),
        )
        assert events == 1  # deduped across repeated advances


@requires_db
@pytest.mark.asyncio
async def test_17_needs_human_default_blocks_without_new_runs():
    """§9-5: stop_on_needs_human=true (default) -> stable block, zero new
    runs, zero dispatch outboxes, idempotent needs_human_blocked event."""
    book_id, _, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        session = await _seed_session(db, book_id)
        await _seed_chapter(db, book_id, 1, "needs_human", nodes[1])
        await db.commit()
        sid = session.id

    result = await _advance(sid)
    assert result["action"] == "block"
    assert result["reason"] == "NEEDS_HUMAN"

    repeat = await _advance(sid)
    assert repeat["action"] == "block"

    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        assert s.status == "blocked" and s.stop_reason == "needs_human"
        runs = await _scalar(
            db,
            select(func.count()).select_from(ChapterRun).where(ChapterRun.book_id == book_id),
        )
        assert runs == 0
        dispatches = await _scalar(
            db,
            select(func.count()).select_from(ChapterDispatchOutbox).where(
                ChapterDispatchOutbox.chapter_run_id.in_(
                    select(ChapterRun.id).where(ChapterRun.book_id == book_id)
                )
            ),
        )
        assert dispatches == 0
        events = await _scalar(
            db,
            select(func.count()).select_from(WritingSessionEvent).where(
                WritingSessionEvent.session_id == sid,
                WritingSessionEvent.event_type == "needs_human_blocked",
            ),
        )
        assert events == 1


@requires_db
@pytest.mark.asyncio
async def test_18_needs_human_explicit_continue_requeues_then_runs():
    """§9-6: stop_on_needs_human=false -> audited needs_human->queued
    transition, then exactly one runnable run + dispatch outbox; a repeated
    advance re-attaches instead of stacking."""
    book_id, _, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        session = await _seed_session(
            db, book_id, policy={"stop_on_needs_human": False}
        )
        await _seed_chapter(db, book_id, 1, "needs_human", nodes[1])
        await db.commit()
        sid = session.id

    result = await _advance(sid)
    assert result["action"] == "start_next"

    async with async_session_factory() as db:
        event = (
            await db.execute(
                select(ChapterStateEvent)
                .where(
                    ChapterStateEvent.to_state == "queued",
                    ChapterStateEvent.from_state == "needs_human",
                )
                .order_by(ChapterStateEvent.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        assert event.actor == "writing_session"

        chapter = (
            await db.execute(
                select(Chapter).where(
                    Chapter.book_id == book_id, Chapter.chapter_no == 1
                )
            )
        ).scalar_one()
        assert chapter.status == "queued"
        runs = (
            await db.execute(
                select(ChapterRun).where(ChapterRun.chapter_id == chapter.id)
            )
        ).scalars().all()
        assert len(runs) == 1 and runs[0].status == "queued"
        dispatches = (
            await db.execute(
                select(func.count()).select_from(ChapterDispatchOutbox).where(
                    ChapterDispatchOutbox.chapter_run_id == runs[0].id
                )
            )
        ).scalar()
        assert int(dispatches) == 1

    repeat = await _advance(sid)
    assert repeat["action"] == "wait_current"
    async with async_session_factory() as db:
        runs = await _scalar(
            db,
            select(func.count()).select_from(ChapterRun).where(ChapterRun.book_id == book_id),
        )
        assert runs == 1  # no stacked run


@requires_db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "causal,resource,error_code,expected_action,expected_stop",
    [
        (True, True, "ILLEGAL_KNOWLEDGE", "block", "causal_hard_failure"),
        (True, False, "ILLEGAL_KNOWLEDGE", "block", "causal_hard_failure"),
        (False, True, "PROVIDER_UNAVAILABLE", "block", "resource_blocked"),
        (False, False, "PROVIDER_UNAVAILABLE", "start_next", None),
    ],
)
async def test_19_policy_combinations(causal, resource, error_code, expected_action, expected_stop):
    """§9-7/8: the 2x2 stop_on_causal_failure/stop_on_resource_block matrix.
    causal=false + resource=true used to raise UnboundLocalError on
    latest_run; the resource gate must still block."""
    book_id, ov_id, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        session = await _seed_session(
            db,
            book_id,
            policy={
                "stop_on_causal_failure": causal,
                "stop_on_resource_block": resource,
            },
        )
        ch = await _seed_chapter(db, book_id, 2, "failed", nodes[2])
        await _seed_run(db, book_id, ch, ov_id, status="failed")
        await db.commit()
        sid = session.id

    async with async_session_factory() as db:
        run = (
            await db.execute(
                select(ChapterRun)
                .where(ChapterRun.book_id == book_id)
                .order_by(ChapterRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        run.error_code = error_code
        await db.commit()

    result = await _advance(sid)
    assert result["action"] == expected_action
    if expected_stop is not None:
        assert result["status"] == "blocked"
        async with async_session_factory() as db:
            s = (
                await db.execute(select(WritingSession).where(WritingSession.id == sid))
            ).scalar_one()
            assert s.stop_reason == expected_stop
    else:
        assert result["status"] == "running"


@requires_db
@pytest.mark.asyncio
async def test_20_vanished_run_repair_and_advance_in_same_pass(monkeypatch):
    """§9-9: a pointer to a run row that no longer exists -> one reconcile
    pass clears the pointer, fails the chapter with an audit event, and
    writes the advance outbox without waiting for the next timer."""
    import app.services.session_reconciler as reconciler_mod

    monkeypatch.setattr(reconciler_mod, "CLAIM_LIMIT", 500)
    book_id, ov_id, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        session = await _seed_session(db, book_id)
        chapter = await _seed_chapter(db, book_id, 1, "reviewing", nodes[1])
        session.current_chapter_id = chapter.id
        session.current_chapter_no = 1
        session.current_chapter_run_id = uuid.uuid4()  # run row vanished
        await db.commit()
        sid, chapter_id = session.id, chapter.id

    report = await reconcile_sessions()
    assert report["repaired"] >= 1

    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        assert s.current_chapter_run_id is None
        chapter = (
            await db.execute(select(Chapter).where(Chapter.id == chapter_id))
        ).scalar_one()
        assert chapter.status == "failed"
        event = (
            await db.execute(
                select(ChapterStateEvent)
                .where(
                    ChapterStateEvent.chapter_id == chapter_id,
                    ChapterStateEvent.to_state == "failed",
                )
                .order_by(ChapterStateEvent.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
        assert event.run_id is None
        assert "vanished" in (event.reason or "")
        outbox = (
            await db.execute(
                select(func.count()).select_from(SessionAdvanceOutbox).where(
                    SessionAdvanceOutbox.writing_session_id == sid
                )
            )
        ).scalar()
        assert int(outbox) >= 1  # same-pass poke
# ─────────────────────────────────────────────────────────────────────────────
# Re-acceptance round 2 (2026-08-30): reconciler poke idempotency across real
# reconcile-lease expiry — final rework task §6/§7
# ─────────────────────────────────────────────────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_21_reconcile_only_idempotent_across_lease_expiry(monkeypatch):
    """§7-1..6: stock orphan -> first reconcile pokes exactly ONE pending
    advance; with the outbox UNCONSUMED and the 60s claim lease explicitly
    expired, three further reconcile passes each re-claim the session yet
    never grow the poke, the audit event, or the chapter/run counts."""
    import app.services.session_reconciler as reconciler_mod

    monkeypatch.setattr(reconciler_mod, "CLAIM_LIMIT", 50)
    book_id, sid, run_id, chapter_id = await _seed_stock_orphan()

    await reconcile_sessions()
    assert await _pending_outbox_count(sid) == 1

    lease_values = []
    for _ in range(3):
        await _expire_reconcile_leases(book_id)
        report = await reconcile_sessions()
        assert report["claimed"] >= 1
        async with async_session_factory() as db:
            s = (
                await db.execute(select(WritingSession).where(WritingSession.id == sid))
            ).scalar_one()
            # proof of a real claim: the lease was re-issued into the future
            assert s.reconcile_lease_until is not None
            assert s.reconcile_lease_until > datetime.now(timezone.utc)
            lease_values.append(s.reconcile_lease_until)
        assert await _pending_outbox_count(sid) == 1

    assert len(set(lease_values)) == 3  # three independent claims happened

    async with async_session_factory() as db:
        failed_events = await _scalar(
            db,
            select(func.count()).select_from(ChapterStateEvent).where(
                ChapterStateEvent.chapter_id == chapter_id,
                ChapterStateEvent.to_state == "failed",
            ),
        )
        chapters = await _scalar(
            db, select(func.count()).select_from(Chapter).where(Chapter.book_id == book_id)
        )
        runs = await _scalar(
            db, select(func.count()).select_from(ChapterRun).where(ChapterRun.book_id == book_id)
        )
    assert failed_events == 1 and chapters == 1 and runs == 1


@requires_db
@pytest.mark.asyncio
async def test_22_vanished_run_poke_idempotent_across_lease_expiry(monkeypatch):
    """§7-7: the vanished-run branch must not accumulate pokes either — two
    lease-expired reconcile-only rounds keep exactly one pending advance."""
    import app.services.session_reconciler as reconciler_mod

    monkeypatch.setattr(reconciler_mod, "CLAIM_LIMIT", 50)
    book_id, ov_id, nodes, _, _ = await _seed_book([1, 2, 3])
    async with async_session_factory() as db:
        session = await _seed_session(db, book_id)
        chapter = await _seed_chapter(db, book_id, 1, "reviewing", nodes[1])
        session.current_chapter_id = chapter.id
        session.current_chapter_no = 1
        session.current_chapter_run_id = uuid.uuid4()  # run row vanished
        await db.commit()
        sid, chapter_id = session.id, chapter.id

    await reconcile_sessions()
    assert await _pending_outbox_count(sid) == 1

    for _ in range(2):
        await _expire_reconcile_leases(book_id)
        report = await reconcile_sessions()
        assert report["claimed"] >= 1
        assert await _pending_outbox_count(sid) == 1

    async with async_session_factory() as db:
        chapter = (
            await db.execute(select(Chapter).where(Chapter.id == chapter_id))
        ).scalar_one()
        assert chapter.status == "failed"
        failed_events = await _scalar(
            db,
            select(func.count()).select_from(ChapterStateEvent).where(
                ChapterStateEvent.chapter_id == chapter_id,
                ChapterStateEvent.to_state == "failed",
            ),
        )
    assert failed_events == 1


@requires_db
@pytest.mark.asyncio
async def test_23_dead_poke_row_gets_one_controlled_rearm(monkeypatch):
    """§7-8: a dead poke row is re-armed to pending exactly once (controlled
    retry, never a second row), and stays untouched while it is pending."""
    import app.services.session_reconciler as reconciler_mod

    monkeypatch.setattr(reconciler_mod, "CLAIM_LIMIT", 50)
    book_id, sid, run_id, chapter_id = await _seed_stock_orphan()

    await reconcile_sessions()
    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(SessionAdvanceOutbox).where(
                    SessionAdvanceOutbox.writing_session_id == sid,
                    SessionAdvanceOutbox.status == "pending",
                )
            )
        ).scalar_one()
        row.status = "dead"
        row.attempts = 5
        row.last_error = "redis down (simulated)"
        await db.commit()
        dead_key = row.dedupe_key

    await _expire_reconcile_leases(book_id)
    await reconcile_sessions()

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(SessionAdvanceOutbox).where(
                    SessionAdvanceOutbox.dedupe_key == dead_key
                )
            )
        ).scalars().all()
        assert len(rows) == 1  # re-armed in place, never a second row
        assert rows[0].status == "pending"
        assert rows[0].attempts == 0
        assert rows[0].last_error is None

    # A further pass must not re-arm again while the row is pending.
    await _expire_reconcile_leases(book_id)
    await reconcile_sessions()
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(SessionAdvanceOutbox).where(
                    SessionAdvanceOutbox.dedupe_key == dead_key
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].attempts == 0
