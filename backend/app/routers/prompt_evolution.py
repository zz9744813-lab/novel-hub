"""v9.7 Prompt Evolution API (spec §32)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import PromptEvolutionCandidate, PromptEvolutionRun, PromptTemplateVersion
from app.prompt_evolution import controller as evo

router = APIRouter(prefix="/api/prompt-evolution", tags=["prompt-evolution"])


def _ser_run(r: PromptEvolutionRun) -> dict:
    return {
        "id": str(r.id),
        "book_id": str(r.book_id),
        "target_role": r.target_role,
        "trigger_code": r.trigger_code,
        "status": r.status,
        "winner_candidate_id": str(r.winner_candidate_id) if r.winner_candidate_id else None,
        "result_json": r.result_json,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/books/{book_id}/status")
async def pe_status(book_id: str, db: AsyncSession = Depends(get_db)):
    runs = (
        (
            await db.execute(
                select(PromptEvolutionRun)
                .where(PromptEvolutionRun.book_id == uuid.UUID(book_id))
                .order_by(PromptEvolutionRun.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    return {"items": [_ser_run(r) for r in runs]}


@router.get("/books/{book_id}/signals")
async def pe_signals(book_id: str, agent_role: str | None = None, db: AsyncSession = Depends(get_db)):
    from app.models import QualitySignal
    from app.quality.signals import digest

    rows = (
        (
            await db.execute(
                select(QualitySignal).where(QualitySignal.book_id == uuid.UUID(book_id))
                .order_by(QualitySignal.created_at.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    sigs = [r for r in rows if not agent_role or r.agent_role == agent_role]
    return {"metrics": digest(sigs)}


@router.post("/books/{book_id}/evaluate-triggers")
async def pe_evaluate(book_id: str, target_role: str, db: AsyncSession = Depends(get_db)):
    result = await evo.evaluate_triggers(db, uuid.UUID(book_id), target_role)
    await db.commit()
    return result


@router.get("/proposals")
async def pe_proposals(db: AsyncSession = Depends(get_db)):
    runs = (await db.execute(select(PromptEvolutionRun).order_by(PromptEvolutionRun.created_at.desc()).limit(50))).scalars().all()
    return {"items": [_ser_run(r) for r in runs]}


@router.get("/proposals/{run_id}")
async def pe_proposal(run_id: str, db: AsyncSession = Depends(get_db)):
    run = (await db.execute(select(PromptEvolutionRun).where(PromptEvolutionRun.id == uuid.UUID(run_id)))).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "proposal not found")
    candidates = (
        (await db.execute(select(PromptEvolutionCandidate).where(PromptEvolutionCandidate.run_id == run.id)))
        .scalars()
        .all()
    )
    return {
        **_ser_run(run),
        "candidates": [
            {
                "id": str(c.id),
                "candidate_version": c.candidate_version,
                "status": c.status,
                "result_json": c.result_json,
                "system_prompt": c.system_prompt,
            }
            for c in candidates
        ],
    }


@router.post("/proposals/{run_id}/generate-candidates")
async def pe_generate(run_id: str, db: AsyncSession = Depends(get_db)):
    run = (await db.execute(select(PromptEvolutionRun).where(PromptEvolutionRun.id == uuid.UUID(run_id)))).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "proposal not found")
    candidates = await evo.generate_candidates(db, run)
    await db.commit()
    return {"candidates": candidates}


@router.post("/proposals/{run_id}/run-regression")
async def pe_regression(run_id: str, candidate_id: str, db: AsyncSession = Depends(get_db)):
    candidate = (await db.execute(select(PromptEvolutionCandidate).where(PromptEvolutionCandidate.id == uuid.UUID(candidate_id)))).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(404, "candidate not found")
    result = await evo.run_regression(db, candidate)
    await db.commit()
    return result


@router.post("/proposals/{run_id}/start-canary")
async def pe_canary(run_id: str, candidate_id: str, db: AsyncSession = Depends(get_db)):
    candidate = (await db.execute(select(PromptEvolutionCandidate).where(PromptEvolutionCandidate.id == uuid.UUID(candidate_id)))).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(404, "candidate not found")
    result = await evo.start_canary(db, candidate)
    await db.commit()
    return result


@router.post("/proposals/{run_id}/promote")
async def pe_promote(run_id: str, prompt_version_id: str, db: AsyncSession = Depends(get_db)):
    version = (await db.execute(select(PromptTemplateVersion).where(PromptTemplateVersion.id == uuid.UUID(prompt_version_id)))).scalar_one_or_none()
    if version is None:
        raise HTTPException(404, "version not found")
    result = await evo.promote_canary(db, version)
    await db.commit()
    return result


@router.post("/proposals/{run_id}/rollback")
async def pe_rollback(run_id: str, prompt_version_id: str, db: AsyncSession = Depends(get_db)):
    version = (await db.execute(select(PromptTemplateVersion).where(PromptTemplateVersion.id == uuid.UUID(prompt_version_id)))).scalar_one_or_none()
    if version is None:
        raise HTTPException(404, "version not found")
    result = await evo.rollback_canary(db, version)
    await db.commit()
    return result
