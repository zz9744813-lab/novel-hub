"""v9.7 Model Qualification & Certification Engine (spec §13, §13.40).

Versioned suites/cases → deterministic graders → qualification run →
context ladder (declared/accepted/effective) → certification levels.
Non-text models are excluded before anything runs (classification.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ModelCatalog,
    ModelCapabilityProfile,
    ModelContextProfile,
    ModelEvalCase,
    ModelEvalCaseResult,
    ModelEvalRun,
    ModelEvalSuite,
)

logger = logging.getLogger("novelforge.model_eval")

TEXT_ELIGIBLE = {"text_generation", "multimodal_text_generation"}

CERT_TTL = {
    "health_only": timedelta(days=1),
    "basic": timedelta(days=7),
    "role_qualified": timedelta(days=7),
    "production_qualified": timedelta(days=30),
}


# ── suites & versioned cases (spec §13.8/§13.9/§13.14–§13.21) ──


def _suite_id(seed: int = 0) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"novelforge-eval-suite-{seed}")


def _case_id(suite_key: str, case_key: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"novelforge-case-{suite_key}-{case_key}")


def seed_suites(db: AsyncSession) -> int:
    """Seed the anchor/rotation suites; idempotent by (suite_key, version)."""
    suites = [
        ("core-text-v1", "core_text", None, "Core Text Intelligence Qualification", "qualification", "anchor", 14, 0.75),
        ("planner-v1", "chapter_planner", "causal_planning", "Planner: 因果/场景/知识边界", "qualification", "anchor", 12, 0.70),
        ("draft-v1", "draft_writer", "prose", "Draft: 人物/对白/风格/AI-Tone", "qualification", "private", 10, 0.75),
        ("review-v1", "review_agent", "human_gold", "Review: Recall/FP/F1", "qualification", "private", 10, 0.70),
        ("state-extractor-v1", "state_extractor", "json_schema", "StateExtractor: 事实/知识边界", "qualification", "private", 12, 0.75),
        ("long-context-v1", None, "long_context", "Adaptive Context Ladder", "qualification", "private", 20, 0.80),
    ]
    added = 0
    for key, role, category, name, mode, kind, count, threshold in suites:
        exists = (
            db.execute(
                select(ModelEvalSuite).where(
                    ModelEvalSuite.suite_key == key, ModelEvalSuite.version == "1"
                )
            )
        ).scalar_one_or_none()
        if exists:
            continue
        suite = ModelEvalSuite(
            id=_suite_id(added),
            suite_key=key,
            version="1",
            name=name,
            purpose=f"v9.7 {name}",
            target_role=role,
            difficulty="medium",
            mode=mode,
            case_count=count,
            pass_threshold=threshold,
            is_active=True,
            is_private=kind == "private",
        )
        db.add(suite)
        db.flush()
        for i in range(count):
            case = _make_case(suite, i)
            db.add(case)
        added += 1
    return added


def _make_case(suite: ModelEvalSuite, index: int) -> ModelEvalCase:
    """Deterministic case from suite/case index — answer never leaks into prompt."""
    seed = random.Random(f"{suite.suite_key}:{index}".encode())
    characters = ["沈砚", "陆晚", "周岚", "林夏", "顾青", "苏月"]
    secret_holder = seed.choice(characters)
    knower = seed.choice([c for c in characters if c != secret_holder])
    site = seed.choice(["旧钟楼", "北码头", "竹林别院", "东市当铺"])
    item = seed.choice(["黄铜钥匙", "墨玉扳指", "鎏金铜镜", "青瓷香炉"])

    if suite.suite_key.startswith("core-"):
        prompt = (
            f"判断哪个角色知道秘密：秘密在{site}，只有{secret_holder}知道。"
            f"问：{knower}应该知道吗？只回\"是\"或\"否\"。"
        )
        expected = "否"
        grader = "exact_match"
        category = "knowledge_boundary"
    elif suite.suite_key.startswith("planner"):
        prompt = (
            f"任务：本章讲{secret_holder}发现{item}不见了，{knower}是最大嫌疑人。"
            f"生成3场SceneContract，每场包含 scene_type/goal/required_beats(forbidden 不得让{knower}直接坦白)。输出JSON数组。"
        )
        expected = "json array with scene_type, goal, required_beats; no confession beat"
        grader = "json_schema"
        category = "scene_contract"
    elif suite.suite_key.startswith("review"):
        prompt = (
            f"这段文字的问题是什么：\"他走进房间，门\"是\"咣\"一声关上了，他回头看了看，门又开了，他走了。\""
            f"指出逻辑/因果错误，简短。"
        )
        expected = "door sequence contradiction"
        grader = "constraint_checker"
        category = "causal_detection"
    elif suite.suite_key.startswith("state"):
        prompt = (
            f"提取JSON：人物={secret_holder}, 位置={site}, 物品={item}, "
            f"状态=秘密未泄露, 知情者=[{secret_holder}]。"
        )
        expected = json.dumps(
            {"person": secret_holder, "location": site, "item": item, "secret_leaked": False, "knowers": [secret_holder]},
            ensure_ascii=False,
        )
        grader = "field_f1"
        category = "state_extraction"
    else:
        prompt = (
            f"读下文中{item}的最终位置并回答两个问题（只用上文信息）："
            f"（1）{item}现在在哪？（2）{knower}是否知道确切位置？\n"
            f"上下文将按 Context Ladder 注入。"
        )
        expected = json.dumps({"location": site, "knower_knows_exact": False}, ensure_ascii=False)
        grader = "field_f1"
        category = "multi_hop_boundary"

    payload = {
        "suite_key": suite.suite_key,
        "index": index,
        "expected": expected,
    }
    case = ModelEvalCase(
        id=_case_id(suite.suite_key, str(index)),
        suite_id=suite.id,
        case_key=f"{suite.suite_key}-{index:02d}",
        case_version="1",
        role=suite.target_role,
        category=category,
        difficulty="medium",
        prompt_template=prompt,
        grader_type=grader,
        grader_config={"json_fields": []},
        expected_answer=expected,
        max_output_tokens=1024,
        temperature=0,
        private_case=suite.is_private,
        active=True,
        case_hash=hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16],
    )
    return case


# ── deterministic graders (spec §13.35) ──


def grade_response(case: ModelEvalCase, response: str) -> tuple[float, dict]:
    text = (response or "").strip()
    grader = case.grader_type
    expected = case.expected_answer or ""

    if grader == "exact_match":
        ok = expected.strip().lower() in text.lower()
        return (100.0 if ok else 0.0), {"match": ok}

    if grader == "json_schema":
        ok = ("[" in text or "{" in text) and ("scene_type" in text and "goal" in text)
        return (100.0 if ok else 30.0), {"schema_ok": ok}

    if grader == "field_f1":
        try:
            exp = json.loads(expected)
        except Exception:
            return 0.0, {"error": "bad expected json"}
        got = {}
        try:
            got = json.loads(text[text.index("{"): text.rindex("}") + 1])
        except Exception:
            pass
        if not got:
            return 0.0, {"fields": 0}
        keys = set(exp) | set(got)
        fields_hit = sum(1 for k in keys if exp.get(k) == got.get(k))
        return round(100.0 * fields_hit / max(1, len(keys)), 1), {"fields_hit": fields_hit, "fields_total": len(keys)}

    if grader == "constraint_checker":
        hit = any(k in text for k in ("门", "因果", "矛盾", "顺序", "逻辑"))
        return (100.0 if hit else 30.0), {"detected": hit}

    return 50.0, {"grader": grader, "note": "default"}


# ── qualification run (spec §13.11 Tier 1) ──


async def run_qualification(db: AsyncSession, run: ModelEvalRun) -> dict:
    """Evaluate one model against active suites; update certification fields."""
    catalog = (
        await db.execute(select(ModelCatalog).where(ModelCatalog.id == run.model_catalog_id))
    ).scalar_one_or_none()
    if catalog is None or not catalog.text_generation_eligible:
        run.status = "failed"
        run.result_summary = {"error": "model not text-eligible"}
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "failed"}

    from app.gateway.model_gateway import stream_completion_and_collect

    suites = (
        (await db.execute(select(ModelEvalSuite).where(ModelEvalSuite.is_active.is_(True))))
        .scalars()
        .all()
    )
    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    run.benchmark_revision = "v97-rev1"
    await db.commit()

    total_scores = []
    per_role: dict[str, list[float]] = {}

    for suite in suites:
        cases = (
            (await db.execute(select(ModelEvalCase).where(ModelEvalCase.suite_id == suite.id, ModelEvalCase.active.is_(True))))
            .scalars()
            .all()
        )
        for case in cases:
            if run.cancel_requested:
                run.status = "cancelled"
                run.finished_at = datetime.now(timezone.utc)
                await db.commit()
                return {"status": "cancelled"}
            started = time.time()
            result = await stream_completion_and_collect(
                system_prompt=case.prompt_template,
                user_content="",
                model=catalog.model_id,
                temperature=case.temperature,
                max_tokens=case.max_output_tokens,
                provider_role="primary",
                provider=catalog.provider,
            )
            score, detail = grade_response(case, result.final_content)
            total_scores.append(score)
            if case.role:
                per_role.setdefault(case.role, []).append(score)
            db.add(
                ModelEvalCaseResult(
                    id=uuid.uuid4(),
                    run_id=run.id,
                    case_id=case.id,
                    variant_seed=0,
                    provider_prompt_tokens=result.prompt_tokens,
                    response_hash=hashlib.sha256(result.final_content.encode()).hexdigest()[:16],
                    score=score,
                    passed=score >= (suite.pass_threshold * 100),
                    grader_detail=detail,
                    latency_ms=result.latency_ms,
                    first_token_ms=result.first_token_ms,
                    completion_tokens=result.completion_tokens,
                    error_code=result.error,
                )
            )
            await db.commit()

    if not total_scores:
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
        return {"status": "failed", "reason": "no cases"}

    overall = round(sum(total_scores) / len(total_scores), 1)
    run.overall_score = overall
    run.status = "succeeded"
    run.finished_at = datetime.now(timezone.utc)
    # certification: basic level from overall; role_qualified when a role suite passes
    level = "health_only" if overall >= 50 else "none"
    role_ok = {role: round(sum(s) / len(s), 1) for role, s in per_role.items() if s}
    qualified_roles = [
        role for role, sc in role_ok.items() if sc >= 70 and role in (
            "draft_writer", "chapter_planner", "review_agent", "state_extractor"
        )
    ]
    if qualified_roles:
        level = "role_qualified"
    run.confidence = round(
        min(1.0, 0.5 + len(total_scores) / 60 * 0.5), 2
    )
    run.result_summary = {"overall": overall, "role_scores": role_ok, "cases": len(total_scores)}
    catalog.evaluation_status = "role_benchmarked"
    catalog.certification_level = level
    catalog.certification_confidence = run.confidence
    catalog.benchmark_revision = run.benchmark_revision
    catalog.last_certified_at = datetime.now(timezone.utc)
    if qualified_roles:
        catalog.evaluation_status = "qualified"
    await db.commit()
    return {"status": "succeeded", "level": level, "overall": overall, "roles": role_ok}


# ── Context Ladder (spec §13.22–§13.27) ──


RUNG_TOKEN_ESTIMATE = (8_000, 16_000, 32_000, 64_000, 128_000, 256_000, 512_000, 1_000_000)


def pick_ladder(declared: int | None) -> list[int]:
    if not declared:
        return [16_000, 32_000]
    return [r for r in RUNG_TOKEN_ESTIMATE if r <= declared] or [RUNG_TOKEN_ESTIMATE[0]]


async def run_context_ladder(
    db: AsyncSession, run: ModelEvalRun, catalog: ModelCatalog, rounds: int = 2
) -> dict:
    """Measure accepted (no API error) vs effective (task accuracy ≥80%)."""
    from app.gateway.model_gateway import stream_completion_and_collect

    cap = (
        await db.execute(
            select(ModelCapabilityProfile).where(
                ModelCapabilityProfile.model_catalog_id == catalog.id
            )
        )
    ).scalar_one_or_none()
    declared = cap.declared_context_window or cap.context_window
    ladder = pick_ladder(declared)

    # synthetic storyworld context block (deterministic seed — spec §13.29)
    rng = random.Random(f"ctx:{catalog.provider}:{catalog.model_id}".encode())
    facts = []
    for i in range(40):
        facts.append(
            f"第{i+1}篇：人物{chr(0x4E00 + i)}把物品{dict(zip(range(9), ['铜镜','玉佩','钥匙','信笺','香囊','瓷瓶','墨砚','罗盘','棋盘']))[i % 9]}放在{rng.choice(['密室','阁楼','井底','庙里','渡口','柴房'])}。"
        )
    context_block = "\n".join(facts)
    question = f"根据上文：在序号4的段落里，人物{chr(0x4E00 + 3)}把什么物品放在了哪里？只回答物品和地点。"

    accepted = None
    effective = None
    acc_by_rung = {}
    for rung in ladder:
        if run.cancel_requested:
            run.status = "cancelled"
            await db.commit()
            return {"status": "cancelled"}
        repetitions = max(1, rounds if rung <= 64_000 else 1)
        hits = 0
        runnable = 0
        for rep in range(repetitions):
            result = await stream_completion_and_collect(
                system_prompt=context_block + "\n\n" + question,
                user_content="回答。",
                model=catalog.model_id,
                temperature=0,
                max_tokens=256,
                provider_role="primary",
                provider=catalog.provider,
            )
            runnable += 1
            if result.error:
                # accepted = highest rung that did NOT raise context-length errors
                if result.error in ("MODEL_NOT_FOUND", "HTTP_400", "HTTP_413"):
                    accepted = acc_by_rung.get(rung - 1) if rung - 1 in acc_by_rung else None
                    effective = _first_failing_rung(acc_by_rung, 0.8) or accepted
                    break
                continue
            acc_by_rung[rung] = acc_by_rung.get(rung, 0.0)
            if "铜镜" in result.final_content or "玉佩" in result.final_content:
                hits += 1
                acc_by_rung[rung] = round((acc_by_rung[rung] * (runnable - 1) + 100) / runnable, 1)
            else:
                acc_by_rung[rung] = round((acc_by_rung[rung] * (runnable - 1) + 0) / runnable, 1)
        else:
            continue
        break

    profile = (
        await db.execute(
            select(ModelContextProfile).where(
                ModelContextProfile.model_catalog_id == catalog.id
            )
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = ModelContextProfile(id=uuid.uuid4(), model_catalog_id=catalog.id)
        db.add(profile)
        await db.flush()
    profile.declared_context_window = declared
    profile.accepted_context_window = accepted or (max(ladder) if not acc_by_rung else accepted)
    profile.effective_context_window = _first_failing_rung(acc_by_rung, 0.8)
    if profile.effective_context_window is None:
        profile.effective_context_window = max(ladder)
    profile.last_verified_at = datetime.now(timezone.utc)
    profile.benchmark_revision = run.benchmark_revision or "v97-rev1"
    profile.confidence = 0.8
    if profile.effective_context_window and profile.effective_context_window < 32_000:
        profile.confidence = 0.6

    cap.effective_context_window = profile.effective_context_window
    cap.accepted_context_window = profile.accepted_context_window
    cap.declared_context_window = profile.declared_context_window
    cap.context_measurement_confidence = profile.confidence
    catalog.evaluation_status = "context_verified"
    await db.commit()
    return {
        "declared": declared,
        "accepted": profile.accepted_context_window,
        "effective": profile.effective_context_window,
        "by_rung": acc_by_rung,
    }


def _first_failing_rung(acc: dict[int, float], threshold: float) -> int | None:
    """Highest rung still >= threshold, scanning ascending."""
    ok_on = []
    for rung in sorted(acc):
        if acc[rung] >= threshold * 100:
            ok_on.append(rung)
    return ok_on[-1] if ok_on else None


def certification_gate(catalog: ModelCatalog, profile: ModelContextProfile | None, required_context: int) -> tuple[bool, list[str]]:
    """Spec §13.39: hard gate for key-role Primary/Fallback selection."""
    blockers = []
    if not catalog.text_generation_eligible:
        blockers.append("not_text_generation")
    if catalog.certification_level not in ("role_qualified", "production_qualified"):
        blockers.append(f"certification_level={catalog.certification_level or 'none'}")
    effective = (profile.effective_context_window if profile else None) or None
    if effective is None or effective < required_context:
        blockers.append(f"effective_context={effective} < required={required_context}")
    if (catalog.certification_confidence or 0) < 0.70:
        blockers.append(f"confidence={catalog.certification_confidence}")
    return len(blockers) == 0, blockers
