"""Step Runner with input_hash checkpoints (AI__.md v3.0 §6 / PR-03).

Rules:
- LLM work happens outside DB transactions.
- Successful outputs are immutable (new attempt_no on retry).
- Reuse requires exact (chapter_run_id, step_key, input_hash) success/reused.
- control_requested pause/cancel is checked at step boundaries.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import select, update, func

from app.database import async_session_factory
from app.models import ChapterRun, ChapterStepRun
from app.prompt_runtime import PromptCompileError

logger = logging.getLogger("novelforge.step_runner")

PIPELINE_VERSION = "pipeline-v2"


class ControlRequestedError(Exception):
    def __init__(self, control: str):
        self.control = control
        super().__init__(f"control_requested={control}")


class RetryableStepError(Exception):
    def __init__(self, code: str, detail: dict | None = None):
        self.code = code
        self.detail = detail or {}
        super().__init__(code)


class PermanentStepError(Exception):
    def __init__(self, code: str, detail: dict | None = None):
        self.code = code
        self.detail = detail or {}
        super().__init__(code)


class LeaseLostError(Exception):
    pass


def canonical_hash(payload: Any) -> str:
    """SHA-256 of sorted JSON (orjson-like via stdlib)."""
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass
class StepArtifact:
    step_key: str
    input_hash: str
    output: Any
    output_hash: str | None = None
    reused: bool = False
    step_run_id: uuid.UUID | None = None
    reused_from_step_id: uuid.UUID | None = None


@dataclass
class RunContext:
    book_id: uuid.UUID
    chapter_id: uuid.UUID
    chapter_no: int
    run_id: uuid.UUID | None
    worker_id: str
    pipeline_version: str = PIPELINE_VERSION
    meta: dict = field(default_factory=dict)


async def honor_control_request(run_id: uuid.UUID | None) -> None:
    if not run_id:
        return
    async with async_session_factory() as db:
        run = (
            await db.execute(select(ChapterRun).where(ChapterRun.id == run_id))
        ).scalar_one_or_none()
        if not run:
            return
        ctrl = (run.control_requested or "none").lower()
        if ctrl in ("pause", "cancel"):
            raise ControlRequestedError(ctrl)


async def ensure_lease_alive(run_id: uuid.UUID | None, worker_id: str) -> None:
    """Stop if lease was taken over (B-03)."""
    if not run_id:
        return
    async with async_session_factory() as db:
        run = (
            await db.execute(select(ChapterRun).where(ChapterRun.id == run_id))
        ).scalar_one_or_none()
        if not run:
            return
        now = datetime.now(timezone.utc)
        if run.lease_owner and run.lease_owner != worker_id:
            if run.lease_expires_at and run.lease_expires_at > now:
                raise LeaseLostError(f"lease held by {run.lease_owner}")
        if run.lease_owner == worker_id:
            # heartbeat
            await db.execute(
                update(ChapterRun)
                .where(ChapterRun.id == run_id, ChapterRun.lease_owner == worker_id)
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=90),
                    current_step=run.current_step,
                )
            )
            await db.commit()


async def find_reusable_checkpoint(
    chapter_run_id: uuid.UUID,
    step_key: str,
    input_hash: str,
) -> ChapterStepRun | None:
    async with async_session_factory() as db:
        result = await db.execute(
            select(ChapterStepRun)
            .where(
                ChapterStepRun.chapter_run_id == chapter_run_id,
                ChapterStepRun.step_key == step_key,
                ChapterStepRun.input_hash == input_hash,
                ChapterStepRun.status.in_(("succeeded", "reused")),
            )
            .order_by(ChapterStepRun.completed_at.desc().nullslast())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def _next_attempt_no(db, chapter_run_id: uuid.UUID, step_key: str) -> int:
    n = (
        await db.execute(
            select(func.coalesce(func.max(ChapterStepRun.attempt_no), 0)).where(
                ChapterStepRun.chapter_run_id == chapter_run_id,
                ChapterStepRun.step_key == step_key,
            )
        )
    ).scalar()
    return int(n or 0) + 1


async def create_running_step(
    *,
    chapter_run_id: uuid.UUID,
    step_name: str,
    step_key: str,
    input_hash: str,
) -> ChapterStepRun:
    async with async_session_factory() as db:
        attempt = await _next_attempt_no(db, chapter_run_id, step_key)
        step = ChapterStepRun(
            id=uuid.uuid4(),
            chapter_run_id=chapter_run_id,
            step_name=step_name,
            step_key=step_key,
            attempt_no=attempt,
            status="running",
            input_hash=input_hash,
            started_at=datetime.now(timezone.utc),
        )
        db.add(step)
        await db.execute(
            update(ChapterRun)
            .where(ChapterRun.id == chapter_run_id)
            .values(current_step=step_key)
        )
        await db.commit()
        await db.refresh(step)
        return step


async def record_reuse(
    checkpoint: ChapterStepRun,
    *,
    chapter_run_id: uuid.UUID,
    step_name: str,
    step_key: str,
    input_hash: str,
) -> StepArtifact:
    """Return existing success/reused checkpoint without a new row.

    DB enforces UNIQUE (chapter_run_id, step_key, input_hash) WHERE status in
    (succeeded, reused) — a second success/reused row for the same hash is
    illegal. Reuse is therefore pure read + touch current_step.
    """
    async with async_session_factory() as db:
        await db.execute(
            update(ChapterRun)
            .where(ChapterRun.id == chapter_run_id)
            .values(current_step=step_key)
        )
        await db.commit()

    output = checkpoint.output_json
    if output is None and checkpoint.output_text is not None:
        output = checkpoint.output_text
    # unwrap {"value": ...} if we stored scalars that way
    if isinstance(output, dict) and set(output.keys()) == {"value"}:
        output = output["value"]
    return StepArtifact(
        step_key=step_key,
        input_hash=input_hash,
        output=output,
        output_hash=checkpoint.output_hash,
        reused=True,
        step_run_id=checkpoint.id,
        reused_from_step_id=checkpoint.reused_from_step_id or checkpoint.id,
    )


async def record_success(
    step_id: uuid.UUID,
    *,
    output: Any,
    output_text: str | None = None,
    artifact_ref: dict | None = None,
) -> StepArtifact:
    """Backward-compatible name used by the invariant suite."""
    return await persist_success(step_id, output=output, output_text=output_text, artifact_ref=artifact_ref)


async def persist_success(
    step_id: uuid.UUID,
    *,
    output: Any,
    output_text: str | None = None,
    artifact_ref: dict | None = None,
) -> StepArtifact:
    if isinstance(output, str):
        out_json = None
        out_text = output_text if output_text is not None else output
        ohash = content_hash(out_text or "")
    else:
        out_json = output
        out_text = output_text
        ohash = canonical_hash(output) if output is not None else None

    async with async_session_factory() as db:
        step = (
            await db.execute(select(ChapterStepRun).where(ChapterStepRun.id == step_id))
        ).scalar_one()
        # Immutability: only update if still running
        if step.status != "running":
            raise RuntimeError(f"step {step_id} not running (status={step.status})")
        step.status = "succeeded"
        step.output_json = out_json if isinstance(out_json, dict) or isinstance(out_json, list) else (
            {"value": out_json} if out_json is not None and not isinstance(out_json, (dict, list)) else out_json
        )
        # store structured output as-is when dict/list; wrap scalars
        if isinstance(output, (dict, list)):
            step.output_json = output
        elif output is not None and not isinstance(output, str):
            step.output_json = {"value": output}
        step.output_text = out_text
        step.output_hash = ohash
        step.artifact_ref = artifact_ref
        step.completed_at = datetime.now(timezone.utc)
        await db.commit()
        return StepArtifact(
            step_key=step.step_key,
            input_hash=step.input_hash,
            output=output,
            output_hash=ohash,
            reused=False,
            step_run_id=step.id,
        )


async def persist_failure(
    step_id: uuid.UUID,
    *,
    error_code: str,
    error_detail: dict | None = None,
) -> None:
    async with async_session_factory() as db:
        step = (
            await db.execute(select(ChapterStepRun).where(ChapterStepRun.id == step_id))
        ).scalar_one_or_none()
        if not step:
            return
        if step.status == "running":
            step.status = "failed"
            step.error_code = error_code
            step.error_detail = error_detail or {}
            step.completed_at = datetime.now(timezone.utc)
            await db.commit()


async def run_step(
    *,
    ctx: RunContext,
    step_name: str,
    step_key: str,
    input_payload: dict,
    execute_fn: Callable[[dict], Awaitable[Any]],
    validate_fn: Callable[[Any], Any] | None = None,
    skip_if_no_run: bool = True,
) -> StepArtifact:
    """Run one step with checkpoint reuse (INV-07).

    If ctx.run_id is None and skip_if_no_run, just execute without persistence
    (compat path for legacy tasks without ChapterRun).
    """
    input_hash = canonical_hash(
        {
            "pipeline_version": ctx.pipeline_version,
            "step_key": step_key,
            "payload": input_payload,
        }
    )

    await honor_control_request(ctx.run_id)
    await ensure_lease_alive(ctx.run_id, ctx.worker_id)

    if not ctx.run_id:
        if not skip_if_no_run:
            raise PermanentStepError("missing_chapter_run")
        output = await execute_fn(input_payload)
        if validate_fn:
            output = validate_fn(output)
        return StepArtifact(
            step_key=step_key,
            input_hash=input_hash,
            output=output,
            output_hash=content_hash(output) if isinstance(output, str) else canonical_hash(output),
            reused=False,
        )

    # Reuse exact success
    existing = await find_reusable_checkpoint(ctx.run_id, step_key, input_hash)
    if existing:
        logger.info(
            "checkpoint reuse run=%s step=%s hash=%s from=%s",
            ctx.run_id,
            step_key,
            input_hash[:12],
            existing.id,
        )
        return await record_reuse(
            existing,
            chapter_run_id=ctx.run_id,
            step_name=step_name,
            step_key=step_key,
            input_hash=input_hash,
        )

    step = await create_running_step(
        chapter_run_id=ctx.run_id,
        step_name=step_name,
        step_key=step_key,
        input_hash=input_hash,
    )

    await honor_control_request(ctx.run_id)
    await ensure_lease_alive(ctx.run_id, ctx.worker_id)

    try:
        output = await execute_fn(input_payload)
        if validate_fn:
            output = validate_fn(output)
        art = await persist_success(step.id, output=output)
        return art
    except ControlRequestedError:
        await persist_failure(step.id, error_code="control_requested")
        raise
    except LeaseLostError:
        await persist_failure(step.id, error_code="lease_lost")
        raise
    except RetryableStepError as exc:
        await persist_failure(step.id, error_code=exc.code, error_detail=exc.detail)
        raise
    except PermanentStepError as exc:
        await persist_failure(step.id, error_code=exc.code, error_detail=exc.detail)
        raise
    except PromptCompileError as exc:
        # Deterministic template/configuration errors can never succeed by
        # retrying -- treat them as permanent step failures so the pipeline
        # stops the ChapterRun and blocks the session instead of retrying
        # every outbox tick and accumulating failure steps forever.
        await persist_failure(
            step.id,
            error_code="prompt_compile_error",
            error_detail={"type": type(exc).__name__, "message": str(exc)[:500]},
        )
        raise PermanentStepError("prompt_compile_error", {"message": str(exc)[:500]}) from exc
    except Exception as exc:
        await persist_failure(
            step.id,
            error_code="step_exception",
            error_detail={"type": type(exc).__name__, "message": str(exc)[:500]},
        )
        raise RetryableStepError("step_exception", {"message": str(exc)[:500]}) from exc


async def acquire_run_lease(
    run_id: uuid.UUID,
    worker_id: str,
    lease_seconds: int = 90,
) -> ChapterRun | None:
    """Atomic CAS lease take (AI__.md §7.1 / B-03)."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=lease_seconds)
    async with async_session_factory() as db:
        run = (
            await db.execute(select(ChapterRun).where(ChapterRun.id == run_id))
        ).scalar_one_or_none()
        if not run:
            return None
        if run.status not in ("queued", "running", "retryable", "waiting_dependency", "paused"):
            # allow reclaim only for active-ish
            if run.status not in ("queued", "running", "retryable"):
                return None
        lease_free = (
            run.lease_owner is None
            or run.lease_owner == worker_id
            or run.lease_expires_at is None
            or run.lease_expires_at < now
        )
        if not lease_free:
            return None
        result = await db.execute(
            update(ChapterRun)
            .where(
                ChapterRun.id == run_id,
                ChapterRun.status.in_(("queued", "running", "retryable")),
            )
            .where(
                (ChapterRun.lease_owner.is_(None))
                | (ChapterRun.lease_owner == worker_id)
                | (ChapterRun.lease_expires_at.is_(None))
                | (ChapterRun.lease_expires_at < now)
            )
            .values(
                status="running",
                lease_owner=worker_id,
                lease_expires_at=exp,
                heartbeat_at=now,
                started_at=run.started_at or now,
            )
        )
        if result.rowcount != 1:
            await db.rollback()
            return None
        await db.commit()
        run = (
            await db.execute(select(ChapterRun).where(ChapterRun.id == run_id))
        ).scalar_one_or_none()
        return run


async def release_run_lease(run_id: uuid.UUID, worker_id: str) -> None:
    async with async_session_factory() as db:
        await db.execute(
            update(ChapterRun)
            .where(ChapterRun.id == run_id, ChapterRun.lease_owner == worker_id)
            .values(lease_owner=None, lease_expires_at=None)
        )
        await db.commit()
