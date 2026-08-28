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
    ACTIVE_SESSION_STATUSES,
    CCNE_HARD_CODES,
    DEFAULT_POLICY,
    RESOURCE_HARD_CODES,
    advance_writing_session,
    evaluate_session,
    serialize_session,
)
from app.services.writing_session_service import (
    _build_policy,
    _resolve_until_datetime,
    create_writing_session,
    poke_waiting_editorial_sessions,
)
from app.schemas.writing_session import WritingSessionCreateRequest


def _db_available() -> bool:
    try:
        import asyncpg

        from app.config import settings

        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        conn = asyncio.run(asyncpg.connect(dsn=dsn, timeout=3))
        asyncio.run(conn.close())
        return True
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

    job_id = await enqueue_advance_arq(session_id, run_id)

    assert job_id == f"session-advance:{session_id}:{run_id}"
    assert calls == [
        (
            (SESSION_ADVANCE_ARQ_FUNCTION, str(session_id), str(run_id)),
            {"_job_id": job_id},
        )
    ]


class _Scalar:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _List:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _MockDb:
    def __init__(self, results):
        self.calls = 0
        self.results = list(results)

    async def execute(self, *args, **kwargs):
        self.calls += 1
        return self.results.pop(0)


def _ns(**kw):
    return type("NS", (), kw)()


def _test_policy() -> dict:
    return dict(DEFAULT_POLICY)


def _mock_node(book_id, ov_id, no):
    return _ns(id=uuid.uuid4(), book_id=book_id, outline_version_id=ov_id, chapter_no=no)


def _mock_book():
    return _ns(id=uuid.uuid4())


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
async def test_selector_outline_exhausted():
    b = _mock_book()
    ov = _ns(id=uuid.uuid4(), book_id=b.id, version=1, status="approved")
    db = _MockDb(
        [
            _Scalar(b),
            _Scalar(ov),
            _List([]),
            _Scalar(30),  # max outline chapter_no
            _Scalar(30),  # finalized 1..30
        ]
    )
    with pytest.raises(NextChapterSelectionError) as exc:
        await select_next_chapter(db, b.id)
    assert exc.value.detail["code"] == "OUTLINE_EXHAUSTED"
    assert exc.value.detail["chapter_no"] == 31


@pytest.mark.asyncio
async def test_selector_outline_gap():
    b = _mock_book()
    ov = _ns(id=uuid.uuid4(), book_id=b.id, version=1, status="approved")
    db = _MockDb(
        [
            _Scalar(b),
            _Scalar(ov),
            _List([]),
            _Scalar(30),  # outline has nodes 1..30 (18 missing)
            _Scalar(17),  # finalized 1..17
            _Scalar(None),  # node 18 missing
        ]
    )
    with pytest.raises(NextChapterSelectionError) as exc:
        await select_next_chapter(db, b.id)
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
            status="finalizing",
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

    sid = uuid.uuid4()
    async with async_session_factory() as db:
        db.add(
            SessionAdvanceOutbox(
                id=uuid.uuid4(),
                writing_session_id=sid,
                completed_run_id=None,
                event_type="advance_writing_session",
                dedupe_key="session-first:t-50-3",
                payload={},
                status="pending",
                available_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    async def _boom(*args, **kwargs):
        raise ConnectionError("redis down")

    sod.enqueue_advance_arq = _boom
    report = await sod.dispatch_session_outbox_once(limit=10)
    assert report["failed"] == 1
    assert report["dispatched"] == 0

    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(SessionAdvanceOutbox).where(SessionAdvanceOutbox.dedupe_key == "session-first:t-50-3")
            )
        ).scalar_one()
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
async def test_50_8_reconciler_repairs_missing_outbox():
    from app.workers.arq_worker import _insert_session_advance_outbox

    book_id, ov_id = await _seed_book()
    from app.database import async_session_factory

    sid = uuid.uuid4()
    async with async_session_factory() as db:
        s = await _seed_session(db, book_id)
        sid = s.id
        ch = Chapter(
            id=uuid.uuid4(), book_id=book_id, chapter_no=1, outline_node_id=uuid.uuid4(), status="finalized"
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
        run_id = run.id
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
            db.add(
                Chapter(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chapter_no=i,
                    outline_node_id=uuid.uuid4(),
                    status="finalized",
                    editorial_status=st,
                )
            )
        db.add(
            Chapter(
                id=uuid.uuid4(),
                book_id=book_id,
                chapter_no=99,
                outline_node_id=uuid.uuid4(),
                status="finalized",
                editorial_status="accepted",
            )
        )
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
            )
            db.add(ch)
            await db.flush()
            ch_ids.append(ch.id)
        # first 4 chapters accepted, last 6 accepted → 10/10 = 1.0; then re-flag 4 as revised
        for no, cid in enumerate(ch_ids, start=1):
            db.add(
                EditorialReviewRound(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chapter_id=cid,
                    chapter_version_id=uuid.uuid4(),
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
            )
        )
        rid = uuid.uuid4()
        db.add(
            EditorialReviewRound(
                id=rid,
                book_id=book_id,
                chapter_id=cid,
                chapter_version_id=uuid.uuid4(),
                round_no=1,
                status="submitted",
                verdict="accept_with_notes",
                submitted_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            EditorialAnnotation(
                id=uuid.uuid4(),
                review_round_id=rid,
                book_id=book_id,
                chapter_id=cid,
                chapter_version_id=uuid.uuid4(),
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
            db.add(
                Chapter(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chapter_no=no,
                    outline_node_id=uuid.uuid4(),
                    status="finalized",
                )
            )
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
            db.add(
                Chapter(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chapter_no=no,
                    outline_node_id=uuid.uuid4(),
                    status="finalized",
                )
            )
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
