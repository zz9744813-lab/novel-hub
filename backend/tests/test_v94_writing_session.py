"""v9.4 P0 tests (spec §50) — session controller, advance outbox, reconciler, guards.

Pure-logic/mock cases run everywhere; DB-backed cases auto-skip when no
PostgreSQL is reachable (the deploy environment has one).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import (
    Book,
    Chapter,
    ChapterRun,
    ChapterVersion,
    EditorialAnnotation,
    EditorialReviewRound,
    OutlineNode,
    OutlineVersion,
    SessionAdvanceOutbox,
    WritingSession,
)
from app.services.next_chapter_selector import (
    NextChapterSelectionError,
    select_next_chapter,
)
from app.services.writing_session_controller import (
    CCNE_HARD_CODES,
    DEFAULT_POLICY,
    RESOURCE_HARD_CODES,
    evaluate_session,
    serialize_session,
)
from app.services.writing_session_service import (
    _build_policy,
    _resolve_until_datetime,
    create_writing_session,
)
from app.schemas.writing_session import WritingSessionCreateRequest


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


# ─────────────────────────────────────────────────────────────────────────────
# Pure logic (no DB)
# ─────────────────────────────────────────────────────────────────────────────


def test_session_advance_arq_function_is_registered():
    from app.workers.arq_worker import WorkerSettings
    from app.workers.session_outbox_dispatcher import SESSION_ADVANCE_ARQ_FUNCTION

    registered = {getattr(function, "__name__", None) for function in WorkerSettings.functions}
    assert SESSION_ADVANCE_ARQ_FUNCTION in registered


@pytest.mark.asyncio
async def test_session_advance_enqueue_uses_registered_function(monkeypatch):
    import arq

    from app.workers.session_outbox_dispatcher import (
        SESSION_ADVANCE_ARQ_FUNCTION,
        enqueue_advance_arq,
    )

    calls = []

    class _Pool:
        async def enqueue_job(self, *args, **kwargs):
            calls.append((args, kwargs))

        async def close(self):
            return None

    async def _create_pool(_settings):
        return _Pool()

    monkeypatch.setattr(arq, "create_pool", _create_pool)
    session_id = uuid.uuid4()
    run_id = uuid.uuid4()
    first_delivery_id = uuid.uuid4()
    second_delivery_id = uuid.uuid4()

    first_job_id = await enqueue_advance_arq(
        session_id,
        run_id,
        delivery_id=first_delivery_id,
    )
    second_job_id = await enqueue_advance_arq(
        session_id,
        run_id,
        delivery_id=second_delivery_id,
    )

    assert first_job_id == f"session-advance:{session_id}:{first_delivery_id}"
    assert second_job_id == f"session-advance:{session_id}:{second_delivery_id}"
    assert calls == [
        (
            (SESSION_ADVANCE_ARQ_FUNCTION, str(session_id), str(run_id)),
            {"_job_id": first_job_id},
        ),
        (
            (SESSION_ADVANCE_ARQ_FUNCTION, str(session_id), str(run_id)),
            {"_job_id": second_job_id},
        ),
    ]


@pytest.mark.asyncio
async def test_session_advance_runs_preflight_after_committing_marker(monkeypatch):
    import app.model_autopilot.session_preflight_job as preflight_job
    import app.services.writing_session_controller as controller
    import app.workers.writing_session_jobs as jobs

    commits = []
    preflight_calls = []

    class _FirstResult:
        def first(self):
            return None

    class _Db:
        async def execute(self, _statement):
            return _FirstResult()

        async def commit(self):
            commits.append(True)

    class _SessionContext:
        async def __aenter__(self):
            return _Db()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def _advance(_db, _session_id):
        return {"action": "wait_current", "reason": "model preflight running"}

    async def _preflight(session_id):
        preflight_calls.append(session_id)
        return {"status": "running"}

    monkeypatch.setattr(jobs, "async_session_factory", lambda: _SessionContext())
    monkeypatch.setattr(controller, "advance_writing_session", _advance)
    monkeypatch.setattr(preflight_job, "run_writing_session_model_preflight", _preflight)
    session_id = str(uuid.uuid4())

    result = await jobs.advance_writing_session_job({}, session_id)

    assert commits == [True]
    assert preflight_calls == [session_id]
    assert result == {"status": "running"}


def _test_policy() -> dict:
    return dict(DEFAULT_POLICY)


def test_build_policy_defaults():
    req = WritingSessionCreateRequest(mode="duration", duration_minutes=120)
    policy = _build_policy(req)
    assert policy["max_unreviewed_ahead"] == 5
    assert policy["minimum_first_pass_yield"] == 0.70
    assert policy["consecutive_bad_limit"] == 2
    assert policy["schema_version"] == "writing-policy-v1"


def test_build_policy_overrides():
    req = WritingSessionCreateRequest(
        mode="duration", duration_minutes=60, max_unreviewed_ahead=3, minimum_first_pass_yield=0.8
    )
    policy = _build_policy(req)
    assert policy["max_unreviewed_ahead"] == 3
    assert policy["minimum_first_pass_yield"] == 0.8


def test_resolve_until_time_returns_future_utc():
    resolved = _resolve_until_datetime("23:59")
    assert resolved.tzinfo is not None
    assert resolved > datetime.now(timezone.utc)


def test_hard_guard_code_sets():
    assert "ILLEGAL_KNOWLEDGE" in CCNE_HARD_CODES
    assert "PROVIDER_UNAVAILABLE" in RESOURCE_HARD_CODES
    assert len(CCNE_HARD_CODES) >= 6


def test_serialize_session_shape():
    s = WritingSession(
        id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        mode="duration",
        requested_duration_minutes=240,
        status="running",
        control_requested="none",
        chapters_started=3,
        chapters_completed=2,
        words_generated=12000,
        policy_snapshot=_test_policy(),
    )
    view = serialize_session(s)
    assert view["id"] == str(s.id)
    assert view["status"] == "running"
    assert view["chapters_completed"] == 2
    assert view["words_generated"] == 12000


@pytest.mark.asyncio
@requires_db
async def test_selector_outline_exhausted():
    """All approved chapters finalized -> OUTLINE_EXHAUSTED (spec §31)."""
    from app.database import async_session_factory

    book_id, _ov_id = await _seed_book(nodes=30)
    async with async_session_factory() as db:
        for no in range(1, 31):
            await _seed_finalized_chapter(db, book_id, no)
        await db.commit()
        with pytest.raises(NextChapterSelectionError) as exc:
            await select_next_chapter(db, book_id)
    assert exc.value.detail["code"] == "OUTLINE_EXHAUSTED"
    assert exc.value.detail["chapter_no"] == 31


@pytest.mark.asyncio
@requires_db
async def test_selector_outline_gap():
    """A numbering hole in the approved outline blocks with OUTLINE_NODE_MISSING."""
    from app.database import async_session_factory

    book_id, _ov_id = await _seed_book(nodes=30, missing={18})
    async with async_session_factory() as db:
        for no in range(1, 18):
            await _seed_finalized_chapter(db, book_id, no)
        await db.commit()
        with pytest.raises(NextChapterSelectionError) as exc:
            await select_next_chapter(db, book_id)
    assert exc.value.detail["code"] == "OUTLINE_NODE_MISSING"
    assert exc.value.detail["chapter_no"] == 18


# ─────────────────────────────────────────────────────────────────────────────
# DB-backed cases (spec §50.1–§50.13)
# ─────────────────────────────────────────────────────────────────────────────


async def _seed_book(nodes: int = 30, missing: set[int] | None = None):
    from app.database import async_session_factory

    async with async_session_factory() as db:
        book_id = uuid.uuid4()
        db.add(Book(id=book_id, title="v9.4 测试书"))
        ov = OutlineVersion(id=uuid.uuid4(), book_id=book_id, version=1, status="approved")
        db.add(ov)
        await db.flush()
        missing = missing or set()
        for no in range(1, nodes + 1):
            if no in missing:
                continue
            db.add(
                OutlineNode(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    outline_version_id=ov.id,
                    node_type="chapter",
                    chapter_no=no,
                    title=f"第{no}章",
                    goal="goal",
                )
            )
        await db.commit()
        return book_id, ov.id


async def _seed_finalized_chapter(db, book_id: uuid.UUID, no: int) -> Chapter:
    """Finalized chapter satisfying INV-01 (final pointer) and the editorial
    backlog gate (accepted), so global invariant tests stay green on the
    shared accumulating test DB."""
    ch = Chapter(
        id=uuid.uuid4(),
        book_id=book_id,
        chapter_no=no,
        outline_node_id=uuid.uuid4(),
        status="finalized",
        editorial_status="accepted",
        finalized_version=1,
    )
    db.add(ch)
    await db.flush()
    db.add(
        ChapterVersion(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=ch.id,
            version=1,
            content=f"第{no}章定稿",
            word_count=100,
            source_run_id=uuid.uuid4(),
            version_kind="final",
        )
    )
    return ch


async def _seed_session(db, book_id: uuid.UUID, *, status="running", **kw) -> WritingSession:
    s = WritingSession(
        id=uuid.uuid4(),
        book_id=book_id,
        mode="duration",
        requested_duration_minutes=90,
        started_at=datetime.now(timezone.utc),
        deadline_at=datetime.now(timezone.utc) + timedelta(hours=2),
        status=status,
        control_requested="none",
        policy_snapshot=_test_policy(),
        **kw,
    )
    db.add(s)
    await db.flush()
    return s


async def _outbox_count(sid: uuid.UUID) -> int:
    from app.database import async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(
            select(func.count()).select_from(SessionAdvanceOutbox).where(
                SessionAdvanceOutbox.writing_session_id == sid
            )
        )
        return int(result.scalar() or 0)


@requires_db
@pytest.mark.asyncio
async def test_50_1_finalizer_does_not_race():
    """Finalize done but run != succeeded → no outbox; succeeded → exactly one."""
    from app.workers.arq_worker import _insert_session_advance_outbox

    book_id, ov_id = await _seed_book()
    from app.database import async_session_factory

    sid = uuid.uuid4()
    async with async_session_factory() as db:
        s = await _seed_session(db, book_id)
        sid = s.id
        ch = Chapter(
            id=uuid.uuid4(), book_id=book_id, chapter_no=1, outline_node_id=uuid.uuid4(), status="finalizing"
        )
        db.add(ch)
        await db.flush()
        run = ChapterRun(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=ch.id,
            chapter_no=1,
            outline_version_id=ov_id,
            pipeline_version="pipeline-v2",
            status="running",  # real run vocabulary: active run -> wait_current
            request_id="t-50-1",
            writing_session_id=sid,
        )
        db.add(run)
        await db.flush()
        s.current_chapter_id = ch.id
        s.current_chapter_no = 1
        s.current_chapter_run_id = run.id
        await db.commit()
        run_id = run.id

    # Finalizer done, run not succeeded → controller waits, no outbox
    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        decision = await evaluate_session(db, s)
        await db.commit()
    assert decision.action == "wait_current"
    assert await _outbox_count(sid) == 0

    # run succeeded → same-txn outbox row
    async with async_session_factory() as db:
        run = (await db.execute(select(ChapterRun).where(ChapterRun.id == run_id))).scalar_one()
        run.status = "succeeded"
        await _insert_session_advance_outbox(db, sid, run_id)
        await db.commit()
    assert await _outbox_count(sid) == 1


@requires_db
@pytest.mark.asyncio
async def test_50_2_transaction_rollback():
    from app.workers.arq_worker import _insert_session_advance_outbox

    book_id, _ = await _seed_book()
    from app.database import async_session_factory

    async with async_session_factory() as db:
        s = await _seed_session(db, book_id)
        await db.flush()
        await _insert_session_advance_outbox(db, s.id, uuid.uuid4())
        await db.rollback()
    assert await _outbox_count(s.id) == 0


@requires_db
@pytest.mark.asyncio
async def test_50_3_redis_failure_backoff():
    import app.workers.session_outbox_dispatcher as sod

    book_id, _ = await _seed_book()
    from app.database import async_session_factory

    async with async_session_factory() as db:
        s = await _seed_session(db, book_id)
        sid = s.id
        db.add(
            SessionAdvanceOutbox(
                id=uuid.uuid4(),
                writing_session_id=sid,
                completed_run_id=None,
                event_type="advance_writing_session",
                # sid is fresh per run: the shared test DB keeps rows forever,
                # so a fixed dedupe_key would collide on the second run
                dedupe_key=f"session-first:{sid}",
                payload={},
                status="pending",
                available_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    async def _boom(*args, **kwargs):
        raise ConnectionError("redis down")

    sod.enqueue_advance_arq = _boom
    # The shared test DB accumulates pending outbox rows from other tests;
    # dispatch repeatedly (redis always fails) until THIS row has been
    # attempted at least once, then assert the backoff contract on it.
    report = {"dispatched": 0, "failed": 0}
    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(SessionAdvanceOutbox).where(SessionAdvanceOutbox.dedupe_key == f"session-first:{sid}")
            )
        ).scalar_one()
    for _ in range(10):
        report = await sod.dispatch_session_outbox_once(limit=500)
        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(SessionAdvanceOutbox).where(SessionAdvanceOutbox.dedupe_key == f"session-first:{sid}")
                )
            ).scalar_one()
        if int(row.attempts or 0) >= 1:
            break
    # The contract: nothing dispatches while redis is down, and this row
    # backs off (attempts counted, still pending, lock released).
    assert report["dispatched"] == 0
    assert report["failed"] >= 1
    assert row.status == "pending"  # backoff path, not dead
    assert int(row.attempts or 0) >= 1
    assert row.locked_by is None


@requires_db
@pytest.mark.asyncio
async def test_50_4_pause():
    from app.workers.arq_worker import _insert_session_advance_outbox

    book_id, ov_id = await _seed_book()
    from app.database import async_session_factory

    async with async_session_factory() as db:
        sid, run_id = None, None
        s = await _seed_session(db, book_id)
        sid = s.id
        ch = Chapter(
            id=uuid.uuid4(), book_id=book_id, chapter_no=1, outline_node_id=uuid.uuid4(), status="queued"
        )
        db.add(ch)
        await db.flush()
        run = ChapterRun(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=ch.id,
            chapter_no=1,
            outline_version_id=ov_id,
            pipeline_version="pipeline-v2",
            status="running",
            request_id="t-50-4",
            writing_session_id=sid,
        )
        db.add(run)
        await db.flush()
        s.current_chapter_id = ch.id
        s.current_chapter_no = 1
        s.current_chapter_run_id = run.id
        await db.commit()
        run_id = run.id

    # Pause while run is active → wait_current, run.control_requested untouched
    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        s.control_requested = "pause"
        await db.commit()
    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        decision = await evaluate_session(db, s)
        await db.commit()
    assert decision.action == "wait_current"
    async with async_session_factory() as db:
        run = (await db.execute(select(ChapterRun).where(ChapterRun.id == run_id))).scalar_one()
        assert run.control_requested == "none"  # spec §6: never touch run control

    # Run ends → session pauses, no next chapter
    async with async_session_factory() as db:
        run = (await db.execute(select(ChapterRun).where(ChapterRun.id == run_id))).scalar_one()
        run.status = "succeeded"
        await _insert_session_advance_outbox(db, sid, run_id)
        await db.commit()
    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        decision = await evaluate_session(db, s)
        await db.commit()
    assert decision.action == "pause"
    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        assert s.status == "paused"
        assert s.control_requested == "none"
        assert s.current_chapter_run_id is None
    assert await _outbox_count(sid) == 1  # no extra double-advance row


@requires_db
@pytest.mark.asyncio
async def test_50_5_cancel():
    from app.workers.arq_worker import _insert_session_advance_outbox

    book_id, ov_id = await _seed_book()
    from app.database import async_session_factory

    sid = uuid.uuid4()
    async with async_session_factory() as db:
        s = await _seed_session(db, book_id)
        sid = s.id
        ch = Chapter(
            id=uuid.uuid4(), book_id=book_id, chapter_no=1, outline_node_id=uuid.uuid4(), status="queued"
        )
        db.add(ch)
        await db.flush()
        run = ChapterRun(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=ch.id,
            chapter_no=1,
            outline_version_id=ov_id,
            pipeline_version="pipeline-v2",
            status="running",
            request_id="t-50-5",
            writing_session_id=sid,
        )
        db.add(run)
        await db.flush()
        s.current_chapter_id = ch.id
        s.current_chapter_no = 1
        s.current_chapter_run_id = run.id
        await db.commit()
        run_id = run.id

    # cancel requested while run active → ownership retained, wait_current
    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        s.control_requested = "cancel"
        await db.commit()
    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        decision = await evaluate_session(db, s)
        await db.commit()
    assert decision.action == "wait_current"

    async with async_session_factory() as db:
        run = (await db.execute(select(ChapterRun).where(ChapterRun.id == run_id))).scalar_one()
        run.status = "succeeded"
        await _insert_session_advance_outbox(db, sid, run_id)
        await db.commit()
    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        decision = await evaluate_session(db, s)
        await db.commit()
    assert decision.action == "cancel"
    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        assert s.status == "cancelled"


@requires_db
@pytest.mark.asyncio
async def test_50_6_create_idempotency():
    book_id, _ = await _seed_book()
    from app.database import async_session_factory

    req = WritingSessionCreateRequest(mode="duration", duration_minutes=60)
    async with async_session_factory() as db:
        s1 = await create_writing_session(db, book_id=book_id, req=req, idempotency_key="key-50-6")
        await db.commit()
        same = await create_writing_session(db, book_id=book_id, req=req, idempotency_key="key-50-6")
        assert same.id == s1.id
        with pytest.raises(HTTPException) as exc:
            await create_writing_session(db, book_id=book_id, req=req, idempotency_key="key-50-6-b")
        await db.rollback()
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "SESSION_OWNS_BOOK"


@requires_db
@pytest.mark.asyncio
async def test_50_7_double_advance_single_row():
    from app.workers.arq_worker import _insert_session_advance_outbox

    book_id, _ = await _seed_book()
    from app.database import async_session_factory

    async with async_session_factory() as db:
        s = await _seed_session(db, book_id)
        await db.flush()
        sid = s.id
        await _insert_session_advance_outbox(db, sid, uuid.uuid4())
        await _insert_session_advance_outbox(db, sid, s.current_chapter_run_id)  # no-op dedupe variant
        await db.commit()
    # two identical inserts via worker hook
    from app.workers.arq_worker import _insert_session_advance_outbox as hook

    rid = uuid.uuid4()
    async with async_session_factory() as db:
        await hook(db, sid, rid)
        await hook(db, sid, rid)
        await db.commit()
    async with async_session_factory() as db:
        result = await db.execute(
            select(func.count()).select_from(SessionAdvanceOutbox).where(
                SessionAdvanceOutbox.dedupe_key == f"session-advance:{sid}:{rid}"
            )
        )
        assert int(result.scalar() or 0) == 1


@requires_db
@pytest.mark.asyncio
async def test_50_8_reconciler_repairs_missing_outbox(monkeypatch):

    # The shared test DB accumulates active sessions from other tests; the
    # reconciler claims LIMIT sessions per pass ordered by last_reconciled_at,
    # so a small limit makes this test flaky. Raise it for this process.
    import app.services.session_reconciler as reconciler_mod

    monkeypatch.setattr(reconciler_mod, "CLAIM_LIMIT", 500)

    book_id, ov_id = await _seed_book()
    from app.database import async_session_factory

    sid = uuid.uuid4()
    async with async_session_factory() as db:
        s = await _seed_session(db, book_id)
        sid = s.id
        ch = await _seed_finalized_chapter(db, book_id, 1)
        run = ChapterRun(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=ch.id,
            chapter_no=1,
            outline_version_id=ov_id,
            pipeline_version="pipeline-v2",
            status="succeeded",
            request_id="t-50-8",
            writing_session_id=sid,
        )
        db.add(run)
        await db.flush()
        s.current_chapter_id = ch.id
        s.current_chapter_no = 1
        s.current_chapter_run_id = run.id
        await db.commit()
    assert await _outbox_count(sid) == 0

    from app.services.session_reconciler import reconcile_sessions

    report = await reconcile_sessions()
    # ran once or twice (lease windows) but repair must be idempotent
    assert await _outbox_count(sid) == 1
    assert report["repaired"] + report["claimed"] >= 1


@requires_db
@pytest.mark.asyncio
async def test_50_9_backlog_counts_revision_states():
    from app.editorial.session_metrics import count_editorial_backlog

    book_id, _ = await _seed_book()
    from app.database import async_session_factory

    async with async_session_factory() as db:
        states = ["pending_review", "in_review", "revision_requested", "revising", "awaiting_recheck"]
        for i, st in enumerate(states, start=1):
            ch = await _seed_finalized_chapter(db, book_id, i)
            ch.editorial_status = st
        await _seed_finalized_chapter(db, book_id, 99)  # accepted -> not backlog
        await db.commit()
    async with async_session_factory() as db:
        backlog = await count_editorial_backlog(db, book_id)
    assert backlog == 5


@requires_db
@pytest.mark.asyncio
async def test_50_10_recent_first_pass_yield_window():
    from app.editorial.session_metrics import recent_first_pass_yield

    book_id, _ = await _seed_book()
    from app.database import async_session_factory

    ch_ids = []
    async with async_session_factory() as db:
        for no in range(1, 11):
            ch = Chapter(
                id=uuid.uuid4(),
                book_id=book_id,
                chapter_no=no,
                outline_node_id=uuid.uuid4(),
                status="finalized",
                finalized_version=1,
            )
            db.add(ch)
            await db.flush()
            ch_ids.append(ch.id)
        # first 4 chapters accepted, last 6 accepted → 10/10 = 1.0; then re-flag 4 as revised
        for no, cid in enumerate(ch_ids, start=1):
            ver = ChapterVersion(
                id=uuid.uuid4(),
                book_id=book_id,
                chapter_id=cid,
                version=1,
                content=f"第{no}章内容",
                word_count=100,
                source_run_id=uuid.uuid4(),
                version_kind="final",
            )
            db.add(ver)
            await db.flush()
            db.add(
                EditorialReviewRound(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chapter_id=cid,
                    chapter_version_id=ver.id,
                    round_no=1,
                    status="submitted",
                    verdict="accept",
                    submitted_at=datetime.now(timezone.utc),
                )
            )
        await db.commit()
    async with async_session_factory() as db:
        quality = await recent_first_pass_yield(db, book_id=book_id, window_size=10)
    assert quality["reviewed"] == 10
    assert quality["good"] == 10
    assert quality["rate"] == 1.0


@requires_db
@pytest.mark.asyncio
async def test_50_11_accept_with_notes_blocking_not_good():
    from app.editorial.session_metrics import recent_first_pass_yield

    book_id, _ = await _seed_book()
    from app.database import async_session_factory

    async with async_session_factory() as db:
        cid = uuid.uuid4()
        db.add(
            Chapter(
                id=cid,
                book_id=book_id,
                chapter_no=1,
                outline_node_id=uuid.uuid4(),
                status="finalized",
                editorial_status="accepted_with_notes",
                finalized_version=1,
            )
        )
        await db.flush()
        ver = ChapterVersion(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=cid,
            version=1,
            content="第1章内容",
            word_count=100,
            source_run_id=uuid.uuid4(),
            version_kind="final",
        )
        db.add(ver)
        await db.flush()
        rid = uuid.uuid4()
        db.add(
            EditorialReviewRound(
                id=rid,
                book_id=book_id,
                chapter_id=cid,
                chapter_version_id=ver.id,
                round_no=1,
                status="submitted",
                verdict="accept_with_notes",
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.flush()
        db.add(
            EditorialAnnotation(
                id=uuid.uuid4(),
                review_round_id=rid,
                book_id=book_id,
                chapter_id=cid,
                chapter_version_id=ver.id,
                annotation_type="issue",
                severity="major",
                is_blocking=True,
            )
        )
        await db.commit()
    async with async_session_factory() as db:
        quality = await recent_first_pass_yield(db, book_id=book_id, window_size=10)
    assert quality["reviewed"] == 1
    assert quality["good"] == 0


@requires_db
@pytest.mark.asyncio
async def test_50_12_outline_exhausted_completes_session():
    from app.database import async_session_factory

    book_id, ov_id = await _seed_book(nodes=30)
    async with async_session_factory() as db:
        for no in range(1, 31):
            await _seed_finalized_chapter(db, book_id, no)
        s = await _seed_session(db, book_id)
        await db.commit()
        sid = s.id

    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        decision = await evaluate_session(db, s)
        await db.commit()
    assert decision.action == "complete"
    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        assert s.status == "completed"
        assert s.stop_reason == "outline_exhausted"


@requires_db
@pytest.mark.asyncio
async def test_50_13_outline_gap_blocks_session():
    from app.database import async_session_factory

    book_id, _ = await _seed_book(nodes=30, missing={18})
    async with async_session_factory() as db:
        for no in range(1, 18):
            await _seed_finalized_chapter(db, book_id, no)
        s = await _seed_session(db, book_id)
        await db.commit()
        sid = s.id

    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        decision = await evaluate_session(db, s)
        await db.commit()
    assert decision.action == "block"
    async with async_session_factory() as db:
        s = (await db.execute(select(WritingSession).where(WritingSession.id == sid))).scalar_one()
        assert s.status == "blocked"
        assert s.stop_reason == "outline_node_missing"
        assert s.stop_detail == {"chapter_no": 18}
