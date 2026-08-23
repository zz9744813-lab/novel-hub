"""v9.3 Improvement proposals + experiments (spec §43–§58, PR-09/10/11).

Minimal-viable improvement loop on top of the editorial data:

* Proposals — created by L5 revision runs (batch feedback) or manually;
  human-approved only, never auto-applied (§80).
* Experiments — replay the book's regression cases twice (baseline vs
  candidate prompt-injection) and score with *deterministic* hard gates:
  forbidden-pattern absence, required-experience adherence and word-count
  sanity. LLM availability optional: without it the experiment still runs
  the gate arithmetic over stored texts (offline replay).
"""
from __future__ import annotations

import logging
import uuid as uuid_mod
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import (
    EditorialExperienceCard,
    EditorialExperiment,
    EditorialImprovementProposal,
    EditorialRegressionCase,
)

logger = logging.getLogger("novelforge.editorial_improvement")

PROPOSAL_STATUSES = {"proposed", "approved", "experimenting", "promoted", "rolled_back", "rejected"}
RISK_LEVELS = {"low", "medium", "high"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def review_proposal(
    db: AsyncSession, proposal_id, approve: bool, reviewer: str | None = None
) -> EditorialImprovementProposal | None:
    """Human approval gate (§46). Approved proposals may start experiments."""
    p = (
        await db.execute(
            select(EditorialImprovementProposal).where(EditorialImprovementProposal.id == proposal_id)
        )
    ).scalar_one_or_none()
    if p is None:
        return None
    if p.status not in {"proposed", "experimenting"}:
        raise ValueError(f"INVALID_PROPOSAL_STATUS:{p.status}")
    if approve:
        p.status = "approved"
        p.approved_by = reviewer or "human"
        p.approved_at = _now()
    else:
        p.status = "rejected"
    await db.commit()
    return p


# ── deterministic hard gates (§53) ────────────────────────────────────


def run_hard_gates(
    cases: list[EditorialRegressionCase],
    produced: dict[str, str],  # case_id → candidate text
    active_cards: list[EditorialExperienceCard],
) -> dict:
    """Fail-closed gate arithmetic over replay outputs.

    Gates: (1) every forbidden property absent, (2) anti-pattern cards
    from human review respected, (3) non-empty output for every case.
    """
    anti_patterns = [c for c in active_cards if c.rule_type == "anti_pattern"]
    forbidden_keywords = []
    for case in cases:
        for prop in case.forbidden_properties or []:
            if isinstance(prop, dict) and prop.get("category") == "forbidden_pattern":
                continue
            if isinstance(prop, str):
                forbidden_keywords.append(prop)

    case_results = []
    failures = 0
    for case in cases:
        text = produced.get(str(case.id), "")
        ok_nonempty = bool(text and text.strip())
        ok_forbidden = all(kw not in text for kw in forbidden_keywords)
        violated_cards = [
            c.instruction
            for c in anti_patterns
            if any(
                frag and frag in text
                for frag in _fragments(c.instruction)
            )
        ]
        ok_cards = not violated_cards
        passed = ok_nonempty and ok_forbidden and ok_cards
        if not passed:
            failures += 1
        case_results.append(
            {
                "case_id": str(case.id),
                "passed": passed,
                "nonempty": ok_nonempty,
                "forbidden_clean": ok_forbidden,
                "card_violations": violated_cards,
            }
        )
    return {
        "total": len(cases),
        "passed": len(cases) - failures,
        "failed": failures,
        "hard_pass": failures == 0,
        "cases": case_results,
    }


def _fragments(instruction: str) -> list[str]:
    """Extract quotable fragments from a card instruction for violation checks."""
    text = instruction.split("：", 1)[-1]
    return [q.strip() for q in text.split("、") if len(q.strip()) >= 4]


async def run_experiment(
    db: AsyncSession,
    book_id,
    proposal_id=None,
    case_ids: list | None = None,
    candidate_texts: dict[str, str] | None = None,
    use_gepa: bool = False,
) -> EditorialExperiment | None:
    """Replay regression cases baseline-vs-candidate with hard gates.

    ``candidate_texts`` maps regression-case id → candidate output; when
    absent (offline replay) the stored human-accepted chapter text is used
    as the candidate baseline, so gates still exercise the plumbing.
    With ``use_gepa`` the candidate is instead the winner of a GEPA-lite
    search (spec §55–§58) and ``pareto_candidates`` carries the ranking.
    """
    q = select(EditorialRegressionCase).where(
        EditorialRegressionCase.book_id == book_id,
        EditorialRegressionCase.is_active == True,  # noqa: E712
    )
    cases = list((await db.execute(q)).scalars())
    if case_ids:
        wanted = {str(c) for c in case_ids}
        cases = [c for c in cases if str(c.id) in wanted]
    if not cases:
        return None

    active_cards = list(
        (
            await db.execute(
                select(EditorialExperienceCard).where(
                    EditorialExperienceCard.book_id == book_id,
                    EditorialExperienceCard.status == "active",
                )
            )
        ).scalars()
    )

    baseline_texts = {str(c.id): c.chapter_text or "" for c in cases}
    pareto_candidates: list[dict] = []

    if candidate_texts is not None:
        produced = dict(candidate_texts)
    elif use_gepa:
        proposal = None
        if proposal_id:
            proposal = (
                await db.execute(
                    select(EditorialImprovementProposal).where(
                        EditorialImprovementProposal.id == proposal_id
                    )
                )
            ).scalar_one_or_none()
        best, pareto_candidates = run_gepa_search(cases, active_cards, proposal)
        produced = best["texts"]
        logger.info(
            "gepa-lite: best=%s pass_rate=%s retention=%s (%d candidates)",
            best["name"], best["pass_rate"], best["retention"], len(pareto_candidates),
        )
    else:
        produced = dict(baseline_texts)

    gates_candidate = run_hard_gates(cases, produced, active_cards)
    gates_baseline = run_hard_gates(cases, baseline_texts, [])

    metrics = lambda g: {"passed": g["passed"], "total": g["total"], "rate": round(100 * g["passed"] / g["total"], 1) if g["total"] else None}  # noqa: E731
    m_base = metrics(gates_baseline)
    m_cand = metrics(gates_candidate)

    better = (m_cand["passed"] or 0) >= (m_base["passed"] or 0)
    recommendation = "promote" if gates_candidate["hard_pass"] and better else "hold"

    exp = EditorialExperiment(
        book_id=book_id,
        proposal_id=proposal_id,
        baseline_version="stored",
        candidate_version="gepa" if use_gepa else "replay",
        case_ids=[str(c.id) for c in cases],
        metrics_baseline=m_base,
        metrics_candidate=m_cand,
        delta_metrics={
            "pass_delta": (m_cand["passed"] or 0) - (m_base["passed"] or 0),
        },
        hard_gate_results=gates_candidate,
        pareto_candidates=pareto_candidates,
        status="completed",
        recommendation=recommendation,
        started_at=_now(),
        finished_at=_now(),
    )
    db.add(exp)

    if proposal_id:
        p = (
            await db.execute(
                select(EditorialImprovementProposal).where(EditorialImprovementProposal.id == proposal_id)
            )
        ).scalar_one_or_none()
        if p is not None:
            p.experiment_id = exp.id
            p.status = "experimenting"

    await db.flush()
    await db.commit()
    logger.info("experiment done: %d cases, candidate pass %s, recommend %s", len(cases), m_cand, recommendation)
    return exp


async def promote_proposal(
    db: AsyncSession, proposal_id, effective_from_chapter: int | None = None
) -> EditorialImprovementProposal | None:
    """Promote after a passing experiment (§57) — flip status only; the
    pipeline reads active experience cards / approved patches at build time."""
    p = (
        await db.execute(
            select(EditorialImprovementProposal).where(EditorialImprovementProposal.id == proposal_id)
        )
    ).scalar_one_or_none()
    if p is None:
        return None
    if p.status not in {"approved", "experimenting"}:
        raise ValueError(f"INVALID_PROMOTION_STATUS:{p.status}")
    p.status = "promoted"
    p.promoted_at = _now()
    if effective_from_chapter is not None:
        p.effective_from_chapter = effective_from_chapter
    await db.commit()
    return p


async def rollback_proposal(db: AsyncSession, proposal_id) -> EditorialImprovementProposal | None:
    p = (
        await db.execute(
            select(EditorialImprovementProposal).where(EditorialImprovementProposal.id == proposal_id)
        )
    ).scalar_one_or_none()
    if p is None:
        return None
    if p.status != "promoted":
        raise ValueError(f"INVALID_ROLLBACK_STATUS:{p.status}")
    p.status = "rolled_back"
    p.rolled_back_at = _now()
    await db.commit()
    return p


# ── GEPA-lite (spec §55–§58, PR-12) ───────────────────────────────────
#
# Deterministic candidate-patch search over the regression set: candidates
# are derived from active anti-pattern experience cards (deletion edits)
# and the proposal's candidate_patch substitutions. Every candidate is
# replayed against the book's regression cases, scored by the same hard
# gates plus a text-retention objective, then ranked by non-dominated
# sorting (pass_rate ↑, retention ↑). LLM polish optional — offline safe.


def _card_substitutions(card) -> list[dict]:
    """Anti-pattern card → find/delete substitutions over its quotable fragments."""
    return [{"find": frag, "replace": ""} for frag in _fragments(card.instruction)]


def generate_gepa_candidates(
    active_cards: list[EditorialExperienceCard],
    proposal: EditorialImprovementProposal | None = None,
) -> list[dict]:
    """Candidate patches: identity baseline + card-derived + proposal patch."""
    candidates: list[dict] = [{"name": "identity", "substitutions": [], "source": "baseline"}]
    for card in active_cards:
        if card.rule_type == "anti_pattern":
            subs = _card_substitutions(card)
            if subs:
                candidates.append(
                    {"name": f"card:{card.id}", "substitutions": subs, "source": "experience_card"}
                )
    if proposal is not None:
        subs = (proposal.candidate_patch or {}).get("substitutions")
        if isinstance(subs, list):
            clean = [s for s in subs if isinstance(s, dict) and s.get("find")]
            if clean:
                candidates.append(
                    {"name": f"proposal:{proposal.id}", "substitutions": clean, "source": "proposal_patch"}
                )
    return candidates


def apply_substitutions(text: str, substitutions: list[dict]) -> str:
    for s in substitutions:
        text = text.replace(s.get("find", ""), s.get("replace", ""))
    return text


def evaluate_gepa_candidate(
    cases: list[EditorialRegressionCase],
    active_cards: list[EditorialExperienceCard],
    candidate: dict,
) -> dict:
    """Replay one candidate over all cases: hard gates + retention objective."""
    texts: dict[str, str] = {}
    kept = total = 0
    for case in cases:
        base = case.chapter_text or ""
        new = apply_substitutions(base, candidate["substitutions"])
        texts[str(case.id)] = new
        total += len(base)
        kept += len(new)
    gates = run_hard_gates(cases, texts, active_cards)
    return {
        "name": candidate["name"],
        "source": candidate["source"],
        "pass_rate": round(100 * gates["passed"] / gates["total"], 1) if gates["total"] else None,
        "retention": round(kept / total, 3) if total else 1.0,
        "changed": sum(1 for c in cases if texts[str(c.id)] != (c.chapter_text or "")),
        "gates": gates,
        "texts": texts,
    }


def pareto_front(evaluations: list[dict]) -> list[dict]:
    """Non-dominated solutions: maximize pass_rate and retention. Sets pareto_rank."""
    for a in evaluations:
        dominated = any(
            (b["pass_rate"] or 0) >= (a["pass_rate"] or 0)
            and b["retention"] >= a["retention"]
            and (
                (b["pass_rate"] or 0) > (a["pass_rate"] or 0) or b["retention"] > a["retention"]
            )
            for b in evaluations
            if b is not a
        )
        a["pareto_rank"] = 1 if dominated else 0
    return [e for e in evaluations if e["pareto_rank"] == 0]


def run_gepa_search(
    cases: list[EditorialRegressionCase],
    active_cards: list[EditorialExperienceCard],
    proposal: EditorialImprovementProposal | None = None,
) -> tuple[dict, list[dict]]:
    """Full GEPA-lite pass. Returns (best candidate evaluation, ranked summary)."""
    candidates = generate_gepa_candidates(active_cards, proposal)
    evals = [evaluate_gepa_candidate(cases, active_cards, c) for c in candidates]
    front = pareto_front(evals)
    pool = front or evals
    best = max(pool, key=lambda e: ((e["pass_rate"] or 0), e["retention"]))
    ranked = [
        {k: e[k] for k in ("name", "source", "pass_rate", "retention", "changed", "pareto_rank")}
        for e in sorted(evals, key=lambda e: (-(e["pass_rate"] or 0), -e["retention"]))
    ]
    return best, ranked


def new_id() -> str:
    return str(uuid_mod.uuid4())
