"""P0-B regression: PromptCompileError must be a permanent step failure,
never an unbounded retryable exception that accumulates failure steps."""
from __future__ import annotations

import uuid

import pytest

import app.database  # conftest patches module
from sqlalchemy import select, text

from app.models import Book, Chapter, ChapterRun, ChapterStepRun
from app.prompt_runtime import PromptCompileError
from app.engine.step_runner import (
    RunContext,
    PermanentStepError,
    RetryableStepError,
    acquire_run_lease,
    release_run_lease,
    run_step,
)


def _make_ctx(rid, book, ch):
    return RunContext(
        run_id=rid,
        book_id=book.id,
        chapter_id=ch.id,
        chapter_no=getattr(ch, "chapter_no", 1) or 1,
        worker_id="worker-a",
        pipeline_version="test",
    )


async def _fresh_run(book, ch):
    async with app.database.async_session_factory() as db:
        ov = (
            await db.execute(text("SELECT id FROM outline_versions WHERE book_id=:b LIMIT 1"), {"b": book.id})
        ).scalar()
        run = ChapterRun(
            id=uuid.uuid4(),
            book_id=book.id,
            chapter_id=ch.id,
            chapter_no=getattr(ch, "chapter_no", 1) or 1,
            outline_version_id=ov,
            status="queued",
            pipeline_version="test",
            request_id=f"t-{uuid.uuid4().hex[:8]}",
            control_requested="none",
            budget_snapshot={},
            model_binding_snapshot={},
        )
        db.add(run)
        await db.commit()
        return run.id


async def _book_chapter():
    try:
        async with app.database.async_session_factory() as db:
            book = (await db.execute(select(Book).limit(1))).scalar_one_or_none()
            if not book:
                pytest.skip("no book")
            ch = (
                await db.execute(
                    select(Chapter).where(Chapter.book_id == book.id).limit(1)
                )
            ).scalar_one_or_none()
            if not ch:
                pytest.skip("no chapter")
            return book, ch
    except Exception as e:  # no DB in local env -> skip like other DB tests
        msg = str(e).lower()
        if any(k in msg for k in ["connect", "operation", "refused", "getaddrinfo", "gaierror", "timeout", "unable", "could not"]):
            pytest.skip(f"database unavailable: {e}")
        raise


@pytest.mark.asyncio
async def test_prompt_compile_error_is_permanent_not_retryable():
    """PromptCompileError raised by execute_fn surfaces as PermanentStepError,
    never RetryableStepError."""
    book, ch = await _book_chapter()
    rid = await _fresh_run(book, ch)
    ctx = _make_ctx(rid, book, ch)
    await acquire_run_lease(rid, "worker-a", lease_seconds=60)

    async def _boom(_):
        raise PromptCompileError("missing user variables: xyz")

    with pytest.raises(PermanentStepError) as excinfo:
        await run_step(
            ctx=ctx,
            step_name="compile_probe",
            step_key="compile:probe:1",
            input_payload={"x": 1},
            execute_fn=_boom,
        )
    assert excinfo.value.code == "prompt_compile_error"

    # Cleanup
    try:
        await release_run_lease(rid, "worker-a")
    except Exception:
        pass


@pytest.mark.asyncio
async def test_prompt_compile_error_records_prompt_compile_code():
    """The failed step record carries error_code=prompt_compile_error, not
    step_exception, so the pipeline can react permanently instead of retrying."""
    book, ch = await _book_chapter()
    rid = await _fresh_run(book, ch)
    ctx = _make_ctx(rid, book, ch)
    await acquire_run_lease(rid, "worker-a", lease_seconds=60)

    async def _boom(_):
        raise PromptCompileError("missing user variables: qq")

    with pytest.raises(PermanentStepError):
        await run_step(
            ctx=ctx,
            step_name="compile_probe",
            step_key="compile:probe:2",
            input_payload={"x": 1},
            execute_fn=_boom,
        )

    async with app.database.async_session_factory() as db:
        row = (
            await db.execute(
                select(ChapterStepRun)
                .where(ChapterStepRun.chapter_run_id == rid)
                .order_by(ChapterStepRun.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        assert row is not None
        assert row.error_code == "prompt_compile_error"
    try:
        await release_run_lease(rid, "worker-a")
    except Exception:
        pass


@pytest.mark.asyncio
async def test_unrelated_exception_still_retryable():
    """A plain RuntimeError (non-deterministic) must remain RetryableStepError,
    proving we did not over-broaden the permanent classification."""
    book, ch = await _book_chapter()
    rid = await _fresh_run(book, ch)
    ctx = _make_ctx(rid, book, ch)
    await acquire_run_lease(rid, "worker-a", lease_seconds=60)

    async def _boom(_):
        raise RuntimeError("network flap")

    with pytest.raises(RetryableStepError) as excinfo:
        await run_step(
            ctx=ctx,
            step_name="compile_probe",
            step_key="compile:probe:3",
            input_payload={"x": 1},
            execute_fn=_boom,
        )
    assert excinfo.value.code == "step_exception"
    try:
        await release_run_lease(rid, "worker-a")
    except Exception:
        pass


@pytest.mark.asyncio
async def test_compile_error_does_not_accumulate_retryable_failures():
    """Two compile failures on the same step key yield two distinct
    prompt_compile_error records (permanent), never infinitely growing
    step_exception retries."""
    book, ch = await _book_chapter()
    rid = await _fresh_run(book, ch)
    ctx = _make_ctx(rid, book, ch)
    await acquire_run_lease(rid, "worker-a", lease_seconds=60)

    async def _boom(_):
        raise PromptCompileError("missing user variables: zz")

    for _ in range(2):
        with pytest.raises(PermanentStepError):
            await run_step(
                ctx=ctx,
                step_name="compile_probe",
                step_key="compile:probe:4",
                input_payload={"x": 1},
                execute_fn=_boom,
            )

    async with app.database.async_session_factory() as db:
        rows = (
            await db.execute(
                select(ChapterStepRun).where(
                    ChapterStepRun.chapter_run_id == rid,
                    ChapterStepRun.step_key == "compile:probe:4",
                )
            )
        ).scalars().all()
        assert len(rows) == 2
        assert all(r.error_code == "prompt_compile_error" for r in rows)
    try:
        await release_run_lease(rid, "worker-a")
    except Exception:
        pass