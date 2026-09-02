"""v9.8 model ability-evidence + lightweight connectivity gate — acceptance tests.

Strategy (task裁定: mock/fake LLM and DB boundaries; no real PostgreSQL):

* The *evidence core* in ``app.model_eval.evidence`` is pure (no DB / no
  network). Its hashing, cache-decision, grading, context-ladder, and current-
  evidence-state logic is exercised directly with in-memory dicts.
* The *engine DB wrappers* (``run_qualification`` / ``run_context_ladder``)
  and the *preflight / router / autoconfig* paths are exercised against a
  faithful in-memory ``FakeAsyncSession`` that parses the small subset of
  SQLAlchemy ``select(...).where(...)`` clauses the code uses. The LLM boundary
  is a **counting fake gateway** so we can assert 0-call cache hits for real,
  including the ORDINARY second-call path (no client/source id injection).
* PostgreSQL-backed integration tests are gated behind ``requires_db`` and
  SKIP cleanly when PG is absent; migration ORM-parity is checked offline by
  importing the ORM models and comparing column sets against the migration
  module source (no DB needed).

Fake-positive tests from the prior round (artificial source-id injection,
manual benchmark_score stamping, pure-core-only context reuse) were REMOVED
and replaced with production-path tests per the repair task.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, BindParameter

from app.model_eval import evidence
from app.model_eval.evidence import (
    ABILITY_EVALUATOR_REVISION,
    CONTEXT_EVALUATOR_REVISION,
    ability_evaluation_key,
    compute_endpoint_identity_hash,
    context_evaluation_key,
    current_evidence_state,
    decide_ability_reuse_with_parts,
    decide_context_reuse_with_parts,
    describe_ability_evidence,
    describe_context_evidence,
    grade_response,
    model_identity_hash,
    normalize_endpoint,
    pick_ladder,
    run_context_ladder_core,
    run_qualification_core,
    suite_aggregate_hash,
)
from app.model_eval.engine import (
    SuiteDefinitionDriftError,
    get_catalog_evidence_state,
    run_qualification,
    run_context_ladder,
    seed_suites,
    _suite_id,
    ensure_v98_suites,
    refresh_endpoint_identity,
)
from app.model_eval.engine import _ability_suite_hash, _context_suite_hash
from app.model_eval.suite_definitions import (
    CONTEXT_SUITE_VERSION,
    ROLE_EVIDENCE_ALIASES,
    ROUTABLE_ROLES,
    SUITE_VERSION,
    qualification_role_for,
    v98_suite_definitions,
)
from app.models import (
    AgentModelBinding,
    ModelCapabilityProfile,
    ModelCatalog,
    ModelContextProfile,
    ModelEvalCase,
    ModelEvalCaseResult,
    ModelEvalRun,
    ModelEvalSuite,
    ModelHealthSnapshot,
    ModelHealthProbe,
    ModelRoleScore,
)


# ───────────────────────── fake async session ─────────────────────────


class _Rows:
    def __init__(self, rows, session):
        self._rows = rows
        self._session = session

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        if not self._rows:
            return None
        if len(self._rows) > 1:
            raise Exception("scalar_one_or_none matched multiple rows")
        return self._rows[0]


def _eval_clause(clause, obj):
    """Evaluate a SQLAlchemy where clause against a Python ORM object."""
    if clause is None:
        return True
    if isinstance(clause, BooleanClauseList):
        return all(_eval_clause(c, obj) for c in clause.clauses)
    if isinstance(clause, BinaryExpression):
        left = clause.left
        right = clause.right
        col_key = getattr(left, "key", None)
        if col_key is None:
            return True
        actual = getattr(obj, col_key, None)
        val = right.value if isinstance(right, BindParameter) else right
        op_name = clause.operator.__name__
        if op_name == "eq":
            return actual == val
        if op_name == "ne":
            return actual != val
        if op_name == "is_":
            literal = str(right).casefold()
            expected = True if literal == "true" else False if literal == "false" else None
            return actual is expected if expected is not None else actual is val
        if op_name == "is_not":
            literal = str(right).casefold()
            expected = True if literal == "true" else False if literal == "false" else None
            return actual is not expected if expected is not None else actual is not val
        # ordered / datetime comparisons used by the concurrency claim
        if op_name in ("ge", "gt", "le", "lt"):
            try:
                if op_name == "ge":
                    return actual >= val
                if op_name == "gt":
                    return actual > val
                if op_name == "le":
                    return actual <= val
                return actual < val
            except Exception:
                return True
        if op_name == "in_op":
            return actual in (val or [])
        return True
    cls_name = type(clause).__name__
    if cls_name in ("IsTrue", "IsFalse"):
        col = getattr(clause, "element", None)
        col_key = getattr(col, "key", None)
        actual = getattr(obj, col_key, None) if col_key else None
        truthy = bool(actual)
        return truthy if cls_name == "IsTrue" else (not truthy)
    return True


class FakeAsyncSession:
    """Minimal in-memory async session for the v9.8 evidence code paths."""

    _MODELS = {
        m.__tablename__: m for m in (
            ModelCatalog, ModelCapabilityProfile, ModelContextProfile,
            ModelEvalCase, ModelEvalCaseResult, ModelEvalRun, ModelEvalSuite,
            AgentModelBinding, ModelHealthProbe, ModelHealthSnapshot,
            ModelRoleScore,
        )
    }

    def __init__(self):
        self._store: dict[str, list] = {t: [] for t in self._MODELS}
        self._commits = 0
        self._flushes = 0
        self.added = []

    def _table(self, model):
        return self._store.setdefault(model.__tablename__, [])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self._flushes += 1
        for obj in self.added:
            tbl = self._table(type(obj))
            if obj not in tbl:
                tbl.append(obj)
        self.added = []

    async def commit(self):
        self._commits += 1
        await self.flush()

    async def rollback(self):
        self.added = []

    async def execute(self, stmt):
        # Non-select statements do not expose raw columns. A SELECT without a
        # WHERE clause is valid and must return the whole in-memory table.
        if not hasattr(stmt, "whereclause"):
            return _Rows([], self)
        try:
            raw = list(stmt._raw_columns)
        except AttributeError:
            return _Rows([], self)
        tname = raw[0].name if raw else None
        model = self._MODELS.get(tname)
        if model is None:
            # support select(ModelX.id) style where the column name is a table
            return _Rows([], self)
        tbl = self._table(model)
        where = stmt.whereclause
        rows = [r for r in tbl if _eval_clause(where, r)] if where is not None else list(tbl)
        return _Rows(rows, self)

    async def get(self, model, ident):
        for r in self._table(model):
            if getattr(r, "id", None) == ident:
                return r
        return None


# ───────────────────────── counting gateway ─────────────────────────


class CountingGateway:
    def __init__(self, responder=None):
        self.calls = 0
        self.responder = responder

    async def __call__(self, *, system_prompt, user_content, model, temperature, max_tokens, provider):
        self.calls += 1
        if self.responder:
            content, err = self.responder()
            return content, err
        # default: return a passing-ish answer for grader-agnostic prompts
        return "ok", None


# ───────────────────────── helpers ─────────────────────────


def make_catalog(model_id="glm-5.2", provider="primary", kind="text_generation", eligible=True, **extra):
    meta = extra.pop("metadata_json", {}) or {}
    # endpoint identity hash lives in metadata_json only (never a column)
    eih = extra.pop("endpoint_identity_hash", None)
    if eih is not None:
        meta = {**meta, "endpoint_identity_hash": eih}
    cat = ModelCatalog(
        id=uuid.uuid4(),
        provider=provider,
        model_id=model_id,
        model_kind=kind,
        text_generation_eligible=eligible,
        availability_status="available",
        enabled=True,
        auto_route_enabled=True,
        metadata_json=meta,
        certification_level=extra.pop("certification_level", "none"),
        ability_evaluation_key=extra.pop("ability_evaluation_key", None),
        context_evaluation_key=extra.pop("context_evaluation_key", None),
    )
    for k, v in extra.items():
        setattr(cat, k, v)
    return cat


def make_run(mode="qualification", catalog=None, **extra):
    run = ModelEvalRun(id=uuid.uuid4(), model_catalog_id=(catalog.id if catalog else uuid.uuid4()), mode=mode)
    for k, v in extra.items():
        setattr(run, k, v)
    return run


def passing_responder():
    """Return answers that pass the v9.8 graders for a representative case set."""

    def _resp():
        # We cannot know the exact case; return a response that satisfies the
        # lenient graders used by most suites (knowledge_boundary / draft /
        # json). The test seeds from the SAME engine, so graders align.
        return (
            "否。scene_type:开场 goal:埋下伏笔 required_beats:[推开门,转身离去].\n"
            "{\"dialogue_ratio\":0.4,\"pov\":\"third\",\"subtext_present\":true}",
            None,
        )

    return _resp


def _defined_case(case_key: str) -> dict:
    for suite in v98_suite_definitions():
        for case in suite["cases"]:
            if case["case_key"] == case_key:
                return case
    raise AssertionError(f"missing synthetic case: {case_key}")


def test_v4_ability_contract_exposes_every_machine_graded_label_without_invalidating_context():
    definitions = v98_suite_definitions()
    ability = [suite for suite in definitions if suite["mode"] == "qualification"]
    context = [suite for suite in definitions if suite["mode"] == "context_ladder"]
    assert SUITE_VERSION == "4"
    assert {suite["version"] for suite in ability} == {"4"}
    assert CONTEXT_SUITE_VERSION == "2"
    assert {suite["version"] for suite in context} == {"2"}
    assert {case["case_version"] for suite in context for case in suite["cases"]} == {"2"}

    required_labels = {
        "core-causal-chain-v2": [
            "inside_access_required",
            "white_token_removed",
            "seal_intact",
            "opened_from_inside",
        ],
        "core-counterfactual-v2": ["blue_seal", "white_token"],
        "planner-contract-chain-v2": [
            "wet_footprints_found",
            "door_lock_checked",
            "night_watchman_suspected",
            "night_watchman_confesses",
            "secret_letter_opened",
        ],
        "state-event-delta-v2": ["door", "open", "lamp", "off", "door_opened", "lamp_extinguished"],
        "style-metrics-v2": ["third_limited", "short", "low"],
        "style-consistency-v2": ["pov", "sentence_length"],
    }
    for case_key, labels in required_labels.items():
        prompt = _defined_case(case_key)["prompt_template"]
        assert all(label in prompt for label in labels), case_key


def test_every_v4_ability_case_has_a_contract_compliant_passing_response():
    responses = {
        "core-causal-chain-v2": (
            '{"outcome":"inside_access_required","chain":['
            '"white_token_removed","seal_intact","opened_from_inside"]}'
        ),
        "core-counterfactual-v2": (
            '{"opens":true,"because":["blue_seal","white_token"]}'
        ),
        "core-knowledge-boundary-v2": (
            '{"answer":"unknown","may_infer":false}'
        ),
        "planner-contract-chain-v2": """[
          {"scene_type":"发现","goal":"发现湿脚印","required_beats":["wet_footprints_found"],
           "forbidden_beats":["night_watchman_confesses","secret_letter_opened"],"knowledge_delta":[],"exit_state":"留下疑问"},
          {"scene_type":"核验","goal":"核对门锁","required_beats":["door_lock_checked"],
           "forbidden_beats":["night_watchman_confesses","secret_letter_opened"],"knowledge_delta":[],"exit_state":"锁况明确"},
          {"scene_type":"推断","goal":"怀疑守夜人","required_beats":["night_watchman_suspected"],
           "forbidden_beats":["night_watchman_confesses","secret_letter_opened"],"knowledge_delta":[],"exit_state":"嫌疑成立"}
        ]""",
        "planner-knowledge-delta-v2": (
            '{"姜遥":{"can":["依据水痕检查柜门"],"cannot":[]},'
            '"陆简":{"can":["依据钟声追查"],"cannot":["不得依据水痕"]}}'
        ),
        "draft-subtext-scene-v2": (
            "姜遥用袖口擦去水痕，指尖却在桌沿停了一瞬。窗纸上映着陆简的影子，她没有抬头，"
            "只把信封推回原位，封蜡依旧完整。陆简问：“昨夜守夜人去了哪边？”她将湿袖口"
            "藏进掌心：“我只听见更鼓，没见他经过。”屋里静了片刻，陆简把目光落到桌角："
            "“那这摊水是谁留下的？”姜遥没有回答，只挪开茶盏，露出木纹上浅淡的一圈痕迹。"
            "两个人都盯着那圈痕迹，谁也没有再追问去向。门外风声擦过石阶，像有人停下又走远，"
            "姜遥把双手收回膝上，仍让那只完整的信封留在他们之间。"
        ),
        "draft-continuity-v2": (
            "铜灯早已熄灭，宋霁借窗缝漏进的月光摸到墙边。她先看见窗闩仍从内侧扣着，"
            "便俯身检查地上的灰，又用指节轻敲木框。“昨夜没人从窗户出去。”她回头说道。"
            "同伴压低声音问她凭什么断定，她指了指完好的闩槽，又沿着门边寻找可见的鞋印。"
            "屋里没有回应，她便停在原地，把每一道能看清的刮痕记下。"
        ),
        "review-gold-f1-v2": (
            '{"issues":["time_order","knowledge_leak"],'
            '"non_issues":["red_clothes"]}'
        ),
        "review-clean-control-v2": (
            '{"issues":[],"non_issues":["ordered_actions","knowledge_boundary"]}'
        ),
        "state-snapshot-v2": (
            '{"location":"北柜","item":"湿钥匙","letter_opened":false,'
            '"knowledge":{"姜遥":["钥匙在北柜"],"陆简":["钥匙存在"]}}'
        ),
        "state-event-delta-v2": (
            '{"new_state":{"door":"open","lamp":"off"},'
            '"events":["door_opened","lamp_extinguished"],'
            '"knowledge_delta":{"姜遥":["灯熄灭"],"陆简":["听见门响"]}}'
        ),
        "style-metrics-v2": (
            '{"pov":"third_limited","dialogue_ratio":0.4,'
            '"sentence_length_band":"short","metaphor_density":"low"}'
        ),
        "style-consistency-v2": (
            '{"more_consistent":"B","reasons":["pov","sentence_length"]}'
        ),
    }
    ability_suites = [
        suite
        for suite in v98_suite_definitions()
        if suite["mode"] == "qualification"
    ]
    seen = set()
    for suite in ability_suites:
        floor = float(suite["pass_threshold"]) * 100
        for case in suite["cases"]:
            response = responses[case["case_key"]]
            score, detail = grade_response(case, response)
            assert score >= floor, (case["case_key"], score, detail)
            seen.add(case["case_key"])
    assert seen == set(responses)


def test_scene_contract_records_prohibitions_without_being_penalized_as_violations():
    case = _defined_case("planner-contract-chain-v2")
    response = """[
      {"scene_type":"发现","goal":"发现湿脚印","required_beats":["wet_footprints_found"],
       "forbidden_beats":["night_watchman_confesses","secret_letter_opened"],"knowledge_delta":[],"exit_state":"留下疑问"},
      {"scene_type":"核验","goal":"核对门锁","required_beats":["door_lock_checked"],
       "forbidden_beats":["night_watchman_confesses","secret_letter_opened"],"knowledge_delta":[],"exit_state":"锁况明确"},
      {"scene_type":"推断","goal":"怀疑守夜人","required_beats":["night_watchman_suspected"],
       "forbidden_beats":["night_watchman_confesses","secret_letter_opened"],"knowledge_delta":[],"exit_state":"嫌疑成立"}
    ]"""
    score, detail = grade_response(case, response)
    assert score == 100.0
    assert detail["forbidden_hits"] == []
    assert all(detail["forbidden_ack"].values())


def test_scene_contract_still_detects_a_prohibited_beat_in_executable_fields():
    case = _defined_case("planner-contract-chain-v2")
    response = """[
      {"scene_type":"发现","goal":"发现湿脚印","required_beats":["wet_footprints_found"],
       "forbidden_beats":["night_watchman_confesses","secret_letter_opened"],"knowledge_delta":[],"exit_state":"留下疑问"},
      {"scene_type":"核验","goal":"核对门锁","required_beats":["door_lock_checked","secret_letter_opened"],
       "forbidden_beats":["night_watchman_confesses","secret_letter_opened"],"knowledge_delta":[],"exit_state":"锁况明确"},
      {"scene_type":"推断","goal":"怀疑守夜人","required_beats":["night_watchman_suspected"],
       "forbidden_beats":["night_watchman_confesses","secret_letter_opened"],"knowledge_delta":[],"exit_state":"嫌疑成立"}
    ]"""
    score, detail = grade_response(case, response)
    assert score < 72.0
    assert "secret_letter_opened" in detail["forbidden_hits"]


@pytest.mark.asyncio
async def test_role_qualification_requires_every_mandatory_case_to_clear_its_floor():
    def suite(key, target, prompts):
        return {
            "suite_key": key,
            "version": "test",
            "target_role": target,
            "difficulty": "test",
            "mode": "qualification",
            "pass_threshold": 0.70,
            "is_active": True,
            "is_private": True,
            "cases": [
                {
                    "case_key": prompt,
                    "case_version": "test",
                    "role": target,
                    "category": "test",
                    "prompt_template": prompt,
                    "expected_answer": "yes",
                    "grader_type": "exact_match",
                    "grader_config": {},
                    "temperature": 0,
                    "max_output_tokens": 8,
                    "active": True,
                }
                for prompt in prompts
            ],
        }

    async def gateway(**kwargs):
        return ("no" if kwargs["user_content"] == "role-fail" else "yes"), None

    result = await run_qualification_core(
        catalog={
            "provider": "test",
            "model_id": "test-model",
            "model_kind": "text_generation",
            "text_generation_eligible": True,
        },
        suites=[
            suite("core-test", None, ["core-pass"]),
            suite("draft-test", "draft_writer", ["role-pass", "role-fail"]),
        ],
        gateway=gateway,
        force=True,
    )
    draft = result["roles"]["draft_writer"]
    assert draft["core_floor_passed"] is True
    assert draft["case_floor_passed"] is False
    assert draft["passed_cases"] == 1
    assert draft["total_cases"] == 2
    assert draft["passed"] is False


@pytest.mark.asyncio
async def test_partial_shared_core_does_not_globally_veto_strong_role_evidence():
    core = {
        "suite_key": "core-partial",
        "version": "test",
        "target_role": None,
        "difficulty": "test",
        "mode": "qualification",
        "pass_threshold": 0.70,
        "is_active": True,
        "is_private": True,
        "cases": [
            {
                "case_key": "core-exact",
                "case_version": "test",
                "role": None,
                "prompt_template": "core-exact",
                "expected_answer": "yes",
                "grader_type": "exact_match",
                "grader_config": {},
                "temperature": 0,
                "max_output_tokens": 8,
                "active": True,
            },
            {
                "case_key": "core-partial",
                "case_version": "test",
                "role": None,
                "prompt_template": "core-partial",
                "expected_answer": '{"a":1,"b":2}',
                "grader_type": "json_exact_fields",
                "grader_config": {"exact_fields": {"a": 1, "b": 2}},
                "temperature": 0,
                "max_output_tokens": 32,
                "active": True,
            },
        ],
    }
    role = {
        "suite_key": "draft-strong",
        "version": "test",
        "target_role": "draft_writer",
        "difficulty": "test",
        "mode": "qualification",
        "pass_threshold": 0.70,
        "is_active": True,
        "is_private": True,
        "cases": [
            {
                "case_key": key,
                "case_version": "test",
                "role": "draft_writer",
                "prompt_template": key,
                "expected_answer": "yes",
                "grader_type": "exact_match",
                "grader_config": {},
                "temperature": 0,
                "max_output_tokens": 8,
                "active": True,
            }
            for key in ("role-one", "role-two")
        ],
    }

    async def gateway(**kwargs):
        if kwargs["user_content"] == "core-partial":
            return '{"a":1,"b":0}', None
        return "yes", None

    result = await run_qualification_core(
        catalog={
            "provider": "test",
            "model_id": "test-model",
            "model_kind": "text_generation",
            "text_generation_eligible": True,
        },
        suites=[core, role],
        gateway=gateway,
        force=True,
    )
    draft = result["roles"]["draft_writer"]
    assert draft["core_score"] == 75.0
    assert draft["core_floor_passed"] is True
    assert draft["case_floor_passed"] is True
    assert draft["passed"] is True


@pytest.mark.asyncio
async def test_v5_to_v6_aggregation_reuses_persisted_case_scores_with_zero_calls():
    db = FakeAsyncSession()
    catalog = make_catalog()
    db._table(ModelCatalog).append(catalog)
    source = make_run(catalog=catalog)
    db._table(ModelEvalRun).append(source)
    await db.commit()
    await run_qualification(
        db,
        source,
        gateway=CountingGateway(responder=passing_responder()),
        force=True,
    )

    v5_key = ability_evaluation_key(
        source.ability_identity_hash,
        source.ability_suite_hash,
        "v98-ability-5",
    )
    source.ability_evaluator_revision = "v98-ability-5"
    source.ability_evaluation_key = v5_key
    source.benchmark_revision = "v98-ability-5"
    source.result_summary = {
        "execution_complete": True,
        "overall": 90.0,
        "roles": {
            "draft_writer": {
                "score": 95.0,
                "role_score": 99.0,
                "core_score": 83.3,
                "threshold": 70.0,
                "passed": False,
                "passed_cases": 2,
                "total_cases": 2,
                "case_floor_passed": True,
                "core_floor_passed": False,
                "sample_count": 5,
            }
        },
        "level": "none",
        "case_count": len(db._table(ModelEvalCaseResult)),
    }
    catalog.ability_evaluation_key = v5_key
    catalog.ability_evaluator_revision = "v98-ability-5"
    catalog.ability_source_run_id = source.id

    derived = make_run(catalog=catalog)
    db._table(ModelEvalRun).append(derived)
    await db.commit()
    no_call = CountingGateway()
    result = await run_qualification(db, derived, gateway=no_call, force=False)

    assert no_call.calls == 0
    assert result["gateway_calls"] == 0
    assert result["reuse_reason"] == "aggregation_reuse"
    assert result["roles"]["draft_writer"]["passed"] is True
    assert catalog.ability_source_run_id == derived.id
    assert derived.ability_source_run_id is None


# ═══════════════════════════════════════════════════════════════════════
# 1. ORDINARY second ability qualify → 0 LLM calls, NO source id injected
#    (engine auto-reuses by current key; P0-1)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ordinary_second_qualify_zero_llm():
    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)

    gw1 = CountingGateway(responder=passing_responder())
    run1 = make_run(catalog=cat)
    db._table(ModelEvalRun).append(run1)
    await db.commit()
    r1 = await run_qualification(db, run1, gateway=gw1, force=False)
    assert r1["status"] == "succeeded"
    assert r1["gateway_calls"] > 0
    assert r1["reused"] is False
    assert cat.ability_evaluation_key is not None

    # SECOND ordinary qualify: brand-new run, NO ability_source_run_id.
    gw2 = CountingGateway()
    run2 = make_run(catalog=cat)
    db._table(ModelEvalRun).append(run2)
    await db.commit()
    r2 = await run_qualification(db, run2, gateway=gw2, force=False)
    assert r2["gateway_calls"] == 0, r2
    assert r2["reused"] is True
    assert r2["reuse_reason"] == "cache_hit"
    assert r2["source_run_id"] == str(run1.id)
    assert gw2.calls == 0


# ═══════════════════════════════════════════════════════════════════════
# 2. ORDINARY second context certify → 0 LLM calls + context key WRITTEN to catalog
#    (P0-1 fix — engine.write context_evaluation_key back to catalog)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ordinary_second_context_certify_zero_llm_and_catalog_written():
    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)
    cap = ModelCapabilityProfile(model_catalog_id=cat.id, declared_context_window=64000)
    db._table(ModelCapabilityProfile).append(cap)

    # First context certify: declares 64000, real ladder should pass → effective 64000
    gw1 = CountingGateway(
        responder=lambda: (
            '{"original_code":"4471","current_code":"8820","source":"documented_reset"}',
            None,
        )
    )
    run1 = make_run(mode="context_ladder", catalog=cat)
    db._table(ModelEvalRun).append(run1)
    await db.commit()
    r1 = await run_context_ladder(db, run1, cat, gateway=gw1, force=False)
    assert r1["gateway_calls"] > 0
    assert cat.context_evaluation_key is not None  # P0-1: written to catalog
    assert cat.context_identity_hash is not None
    assert cat.context_suite_hash is not None

    # Simulate a lost derived projection; cache reuse must rebuild it from the
    # immutable source summary without calling the model again.
    db._store[ModelContextProfile.__tablename__] = []
    cap.accepted_context_window = None
    cap.effective_context_window = None

    # SECOND ordinary certify: new run, no context_source_run_id → 0 calls
    gw2 = CountingGateway()
    run2 = make_run(mode="context_ladder", catalog=cat)
    db._table(ModelEvalRun).append(run2)
    await db.commit()
    r2 = await run_context_ladder(db, run2, cat, gateway=gw2, force=False)
    assert r2["gateway_calls"] == 0, r2
    assert r2["reused"] is True
    assert r2["context_evaluation_key"] == cat.context_evaluation_key
    rebuilt = db._table(ModelContextProfile)[0]
    assert rebuilt.context_source_run_id == run1.id
    assert rebuilt.effective_context_window == 64000


# ═══════════════════════════════════════════════════════════════════════
# 3. force / identity / base-url / suite / evaluator revision trigger re-run;
#    pure time change does NOT
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_force_triggers_retest():
    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)
    gw = CountingGateway(responder=passing_responder())
    run1 = make_run(catalog=cat)
    db._table(ModelEvalRun).append(run1)
    await db.commit()
    await run_qualification(db, run1, gateway=gw, force=False)

    gw2 = CountingGateway(responder=passing_responder())
    run2 = make_run(catalog=cat)
    db._table(ModelEvalRun).append(run2)
    await db.commit()
    r2 = await run_qualification(db, run2, gateway=gw2, force=True)
    assert r2["reused"] is False
    assert r2["reuse_reason"] == "force"
    assert gw2.calls > 0


@pytest.mark.asyncio
async def test_base_url_change_triggers_retest():
    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)
    gw = CountingGateway(responder=passing_responder())
    run1 = make_run(catalog=cat)
    db._table(ModelEvalRun).append(run1)
    await db.commit()
    await run_qualification(db, run1, gateway=gw, force=False)
    key1 = cat.ability_evaluation_key
    identity1 = cat.ability_identity_hash

    # change the real base URL → endpoint identity changes → ability key changes
    cat.metadata_json = {**cat.metadata_json, "base_url": "https://new-endpoint.example.com/v1"}
    gw2 = CountingGateway(responder=passing_responder())
    run2 = make_run(catalog=cat)
    db._table(ModelEvalRun).append(run2)
    await db.commit()
    r2 = await run_qualification(db, run2, gateway=gw2, force=False)
    assert r2["reused"] is False
    assert r2["reuse_reason"] in ("identity_changed", "suite_changed")
    assert "identity" in r2["changed_fields"]
    assert cat.ability_identity_hash != identity1
    assert cat.ability_evaluation_key != key1
    assert gw2.calls > 0


@pytest.mark.asyncio
async def test_identity_revert_reuses_matching_older_content_addressed_source():
    db = FakeAsyncSession()
    cat = make_catalog(metadata_json={"base_url": "https://endpoint-a.example/v1"})
    db._table(ModelCatalog).append(cat)

    first = make_run(catalog=cat)
    db._table(ModelEvalRun).append(first)
    await db.commit()
    await run_qualification(
        db, first, gateway=CountingGateway(responder=passing_responder())
    )

    cat.metadata_json = {**cat.metadata_json, "base_url": "https://endpoint-b.example/v1"}
    second = make_run(catalog=cat)
    db._table(ModelEvalRun).append(second)
    await db.commit()
    await run_qualification(
        db, second, gateway=CountingGateway(responder=passing_responder())
    )

    cat.metadata_json = {**cat.metadata_json, "base_url": "https://endpoint-a.example/v1"}
    third = make_run(catalog=cat)
    db._table(ModelEvalRun).append(third)
    await db.commit()
    no_call_gateway = CountingGateway()
    result = await run_qualification(db, third, gateway=no_call_gateway)
    assert result["reused"] is True
    assert result["source_run_id"] == str(first.id)
    assert result["gateway_calls"] == 0
    assert no_call_gateway.calls == 0


@pytest.mark.asyncio
async def test_api_key_change_does_not_change_identity():
    db = FakeAsyncSession()
    cat = make_catalog(metadata_json={"base_url": "https://api.example.com/v1", "api_key": "AAA"})
    db._table(ModelCatalog).append(cat)
    gw = CountingGateway(responder=passing_responder())
    run1 = make_run(catalog=cat)
    db._table(ModelEvalRun).append(run1)
    await db.commit()
    await run_qualification(db, run1, gateway=gw, force=False)
    eh1 = cat.metadata_json.get("endpoint_identity_hash")

    # change ONLY the api key → endpoint identity hash MUST be unchanged
    cat.metadata_json = {**cat.metadata_json, "api_key": "BBB"}
    eh2 = compute_endpoint_identity_hash(base_url="https://api.example.com/v1", metadata_json=cat.metadata_json)
    assert eh2 == eh1


@pytest.mark.asyncio
async def test_same_version_suite_drift_fails_closed():
    db = FakeAsyncSession()
    await seed_suites(db)
    await db.commit()
    await _mutate_one_case_prompt(db)
    # A published suite version is immutable. Silent same-version mutation is
    # corruption, not a legitimate cache invalidation event.
    with pytest.raises(SuiteDefinitionDriftError, match="immutable suite"):
        await ensure_v98_suites(db)


async def _mutate_one_case_prompt(db):
    cases = db._table(ModelEvalCase)
    if not cases:
        await seed_suites(db)
        cases = db._table(ModelEvalCase)
    c = cases[0]
    c.prompt_template = c.prompt_template + " [REVISED v3]"
    c.case_hash = evidence._sha256(c.prompt_template + (c.expected_answer or ""))


@pytest.mark.asyncio
async def test_time_change_keeps_evidence_valid():
    ih = "identity-hash-x"
    sh = "suite-hash-x"
    key = ability_evaluation_key(ih, sh)
    d = decide_ability_reuse_with_parts(
        prior_identity_hash=ih, prior_suite_hash=sh,
        prior_rev=ABILITY_EVALUATOR_REVISION,
        prior_source_run_id="old-run", prior_status="succeeded",
        identity_hash=ih, suite_hash=sh,
    )
    assert d.reuse is True
    ck = context_evaluation_key(ih, "ctx-hash")
    dc = decide_context_reuse_with_parts(
        prior_identity_hash=ih, prior_context_suite_hash="ctx-hash",
        prior_rev=CONTEXT_EVALUATOR_REVISION,
        prior_source_run_id="old-c", prior_status="succeeded",
        identity_hash=ih, context_suite_hash="ctx-hash",
    )
    assert dc.reuse is True


# ═══════════════════════════════════════════════════════════════════════
# 4. qualification writes ModelRoleScore.benchmark_score; stale key excluded
#    from composite (P0-4)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_qualification_persists_role_scores_and_case_results():
    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)
    gw = CountingGateway(responder=passing_responder())
    run = make_run(catalog=cat)
    db._table(ModelEvalRun).append(run)
    await db.commit()
    r = await run_qualification(db, run, gateway=gw, force=True)
    assert r["status"] == "succeeded"

    # Every routable role has benchmark lineage. Auxiliary roles reuse a core
    # score; they do not add gateway calls or duplicate synthetic cases.
    roles = db._table(ModelRoleScore)
    assert roles, "role scores must be persisted"
    by_role = {row.agent_role: row for row in roles}
    for role in ROUTABLE_ROLES:
        assert role in by_role, f"missing role score for {role}"
        assert by_role[role].benchmark_evidence_key == cat.ability_evaluation_key
        assert by_role[role].benchmark_source_run_id == run.id
        qualification = (by_role[role].detail_json or {}).get("qualification") or {}
        assert qualification.get("evidence_role") == qualification_role_for(role)

    # case results persisted (real evidence trail)
    case_results = db._table(ModelEvalCaseResult)
    assert case_results, "case results must be persisted"
    assert r["gateway_calls"] == len(case_results)
    assert all(cr.score is not None for cr in case_results)
    assert all(cr.response_hash for cr in case_results)  # digest, not private text

    state = await get_catalog_evidence_state(db, cat)
    for role, source_role in ROLE_EVIDENCE_ALIASES.items():
        aliased = state["role_evidence"][role]
        direct = state["role_evidence"][source_role]
        assert aliased["state"] == "valid"
        assert aliased["passed"] is direct["passed"]
        assert aliased["score"] == direct["score"]
        assert aliased["evidence_role"] == source_role
        assert aliased["reused_for_auxiliary_role"] is True


@pytest.mark.asyncio
async def test_auxiliary_role_score_reuses_core_qualification():
    from app.model_autopilot.scoring import compute_role_score

    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)
    source = make_run(catalog=cat)
    db._table(ModelEvalRun).append(source)
    await db.commit()
    result = await run_qualification(
        db,
        source,
        gateway=CountingGateway(responder=passing_responder()),
        force=True,
    )
    calls_before_scoring = result["gateway_calls"]

    row = await compute_role_score(db, cat, "memory_compiler")
    direct = next(r for r in db._table(ModelRoleScore) if r.agent_role == "state_extractor")
    assert row.benchmark_score == direct.benchmark_score
    assert row.benchmark_evidence_key == direct.benchmark_evidence_key
    assert row.benchmark_source_run_id == direct.benchmark_source_run_id
    assert row.detail_json["benchmark_blended"] is True
    assert row.detail_json["qualification_evidence_role"] == "state_extractor"
    assert row.detail_json["qualification_reused"] is True
    assert result["gateway_calls"] == calls_before_scoring


@pytest.mark.asyncio
async def test_low_score_is_reusable_evidence_but_gateway_failure_is_not():
    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)

    low_run = make_run(catalog=cat)
    db._table(ModelEvalRun).append(low_run)
    await db.commit()
    low = await run_qualification(
        db,
        low_run,
        gateway=CountingGateway(responder=lambda: ("not valid JSON", None)),
        force=True,
    )
    assert low["status"] == "succeeded"
    assert low["execution_complete"] is True
    assert not low["qualified_roles"]
    assert cat.ability_source_run_id == low_run.id

    cached = make_run(catalog=cat)
    db._table(ModelEvalRun).append(cached)
    await db.commit()
    no_call = CountingGateway()
    cached_result = await run_qualification(db, cached, gateway=no_call)
    assert cached_result["reused"] is True
    assert no_call.calls == 0

    prior_source = cat.ability_source_run_id
    failed_force = make_run(catalog=cat)
    db._table(ModelEvalRun).append(failed_force)
    await db.commit()
    failed = await run_qualification(
        db,
        failed_force,
        gateway=CountingGateway(responder=lambda: ("", "HTTP_503")),
        force=True,
    )
    assert failed["status"] == "failed"
    assert failed["execution_complete"] is False
    assert failed_force.ability_source_run_id is None
    assert cat.ability_source_run_id == prior_source


@pytest.mark.asyncio
async def test_stale_benchmark_score_excluded_from_composite():
    from app.model_autopilot.scoring import compute_role_score

    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)
    source = make_run(catalog=cat)
    db._table(ModelEvalRun).append(source)
    await db.commit()

    # Establish a real, source-backed ability record through the production
    # wrapper. Merely stamping a key on the catalog must never be sufficient.
    await run_qualification(
        db,
        source,
        gateway=CountingGateway(responder=passing_responder()),
        force=True,
    )
    row = next(r for r in db._table(ModelRoleScore) if r.agent_role == "draft_writer")
    await compute_role_score(db, cat, "draft_writer")
    assert row.detail_json.get("benchmark_blended") is True

    # A stale lineage key is excluded even though the numeric score remains
    # available for audit.
    row.benchmark_score = 100.0
    row.benchmark_evidence_key = "stale-key-xyz"
    await db.flush()
    row2 = await compute_role_score(db, cat, "draft_writer")
    assert row2.detail_json.get("benchmark_blended") is False
    assert row2.benchmark_score == 100.0

    # Restoring the exact current key and source makes it eligible again.
    row2.benchmark_evidence_key = cat.ability_evaluation_key
    await db.flush()
    row3 = await compute_role_score(db, cat, "draft_writer")
    assert row3.detail_json.get("benchmark_blended") is True


# ═══════════════════════════════════════════════════════════════════════
# 5. context ladder REAL measurement: ≥3 rungs/positions; low rung doesn't
#    become effective; API context-length error only affects accepted/effective;
#    four sub-scores written; catalog updated (P0-2)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_context_ladder_measures_rungs_and_writes_subscores():
    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)
    cap = ModelCapabilityProfile(model_catalog_id=cat.id, declared_context_window=64000)
    db._table(ModelCapabilityProfile).append(cap)

    # Strict JSON satisfies position, multi-hop, instruction-retention, and
    # belief-boundary checks at every rung.
    gw = CountingGateway(
        responder=lambda: (
            '{"original_code":"4471","current_code":"8820","source":"documented_reset"}',
            None,
        )
    )
    run = make_run(mode="context_ladder", catalog=cat)
    db._table(ModelEvalRun).append(run)
    await db.commit()
    r = await run_context_ladder(db, run, cat, gateway=gw, force=True)
    assert r["gateway_calls"] >= 3  # multiple rungs probed (not a single probe)
    assert r["effective"] in (32000, 64000)
    prof = db._table(ModelContextProfile)[0]
    # four sub-scores must be written and non-None
    assert prof.position_robustness_score is not None
    assert prof.multi_hop_score is not None
    assert prof.instruction_retention_score is not None
    assert prof.belief_boundary_score is not None


@pytest.mark.asyncio
async def test_context_ladder_fails_closed_when_low_accuracy():
    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)
    cap = ModelCapabilityProfile(model_catalog_id=cat.id, declared_context_window=64000)
    db._table(ModelCapabilityProfile).append(cap)

    # responder returns junk → every rung fails → effective must be None (not 64000)
    gw = CountingGateway(responder=lambda: ("我不知道，全文如下……" * 5, None))
    run = make_run(mode="context_ladder", catalog=cat)
    db._table(ModelEvalRun).append(run)
    await db.commit()
    r = await run_context_ladder(db, run, cat, gateway=gw, force=True)
    assert r["effective"] is None, r  # fail-closed, never 64000
    prof = db._table(ModelContextProfile)[0]
    assert prof.effective_context_window is None
    assert cat.context_evaluation_key is not None  # key still written (so it's auditable as failed)
    assert cat.evaluation_status == "context_failed"


@pytest.mark.asyncio
async def test_context_ladder_error_only_affects_accepted_effective_not_fake_64000():
    suites = [
        {
            "suite_key": "context-v2", "version": "2", "target_role": None,
            "mode": "context", "pass_threshold": 0.80, "is_active": True,
            "cases": [{
                "case_key": "context-v2-00", "case_version": "2", "role": None,
                "category": "multi_hop_boundary",
                "prompt_template": "ctx", "expected_answer": '{"location":"x"}',
                "grader_type": "belief_boundary", "grader_config": {"required_facts": ["x"], "distractor": "none"},
                "temperature": 0, "max_output_tokens": 256, "case_hash": "h",
            }],
        }
    ]
    catalog = {"provider": "primary", "model_id": "glm-5.2", "model_kind": "text_generation"}
    gw_err = CountingGateway(responder=lambda: ("", "context_length_exceeded"))
    # The adaptive ladder starts low and stops at the first provider context
    # rejection; it must not invent acceptance at the declared maximum.
    r = await run_context_ladder_core(
        catalog=catalog, suites=suites, gateway=gw_err, declared_context_window=64000
    )
    assert r["effective_context_window"] is None
    assert r["status"] == "succeeded"  # run completed, but produced no valid evidence
    assert list(r["rung_results"]) == [8000]
    assert r["rung_results"][8000]["error"] == "context_length_exceeded"
    assert gw_err.calls == 1


# ═══════════════════════════════════════════════════════════════════════
# 6. non-text ability/context → 0 gateway in ALL production wrappers (P0-8)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_non_text_model_excluded_everywhere():
    from unittest.mock import AsyncMock, patch
    from app.model_eval import engine as eval_engine

    non_text = make_catalog(model_id="dall-e", kind="image_generation", eligible=False)
    db = FakeAsyncSession()
    db._table(ModelCatalog).append(non_text)
    run = make_run(catalog=non_text)
    db._table(ModelEvalRun).append(run)
    await db.commit()
    r = await run_qualification(db, run)
    assert r["status"] == "failed"
    assert r["gateway_calls"] == 0

    run2 = make_run(mode="context_ladder", catalog=non_text)
    db._table(ModelEvalRun).append(run2)
    await db.commit()
    r2 = await run_context_ladder(db, run2, non_text)
    assert r2["status"] == "failed"
    assert r2["gateway_calls"] == 0

    from app.model_eval.evidence import preflight_evidence_status
    ok, code = preflight_evidence_status(
        catalog={"text_generation_eligible": False},
        requires_context=False, has_valid_ability_key=False, has_valid_context_key=False)
    assert ok is False and code == "NON_TEXT_MODEL"


def test_configured_text_handshake_is_durable_but_provider_metadata_wins():
    from app.model_autopilot.classification import (
        classify_catalog_model,
        promote_configured_text_model,
    )

    catalog = make_catalog(kind="unknown", eligible=False)
    catalog.auto_route_enabled = False
    catalog.classification_source = "unknown"
    catalog.metadata_json = {}

    promote_configured_text_model(catalog)
    assert catalog.model_kind == "text_generation"
    assert catalog.text_generation_eligible is True
    assert catalog.auto_route_enabled is True
    assert catalog.classification_source == "configured_text_handshake"

    classify_catalog_model(catalog)
    assert catalog.text_generation_eligible is True
    assert catalog.classification_source == "configured_text_handshake"

    catalog.metadata_json = {"output_modalities": ["image"]}
    classify_catalog_model(catalog)
    assert catalog.model_kind == "image_generation"
    assert catalog.text_generation_eligible is False


def test_provider_context_metadata_and_unknown_ladder_reach_production_size():
    from app.model_autopilot.catalog import provider_declared_context_window

    assert provider_declared_context_window({"context_length": "131072"}) == 131072
    assert provider_declared_context_window(
        {"capabilities": {"max_input_tokens": 200000}}
    ) == 200000
    assert provider_declared_context_window({"context_window": True}) is None
    assert provider_declared_context_window({"context_window": 99_000_000}) is None
    assert pick_ladder(None) == [8_000, 32_000, 64_000, 128_000]


def test_health_probe_budget_handles_reasoning_models_without_becoming_benchmark(
    monkeypatch,
):
    from app.model_autopilot.probe import (
        _configured_handshake_max_tokens,
        _health_probe_max_tokens,
    )

    monkeypatch.delenv("MODEL_HEALTH_MAX_TOKENS", raising=False)
    assert _health_probe_max_tokens() == 128
    monkeypatch.setenv("MODEL_HEALTH_MAX_TOKENS", "invalid")
    assert _health_probe_max_tokens() == 128
    monkeypatch.setenv("MODEL_HEALTH_MAX_TOKENS", "9999")
    assert _health_probe_max_tokens() == 512
    monkeypatch.delenv("MODEL_HANDSHAKE_MAX_TOKENS", raising=False)
    assert _configured_handshake_max_tokens() == 2048
    monkeypatch.setenv("MODEL_HANDSHAKE_MAX_TOKENS", "99999")
    assert _configured_handshake_max_tokens() == 4096


@pytest.mark.asyncio
async def test_configured_handshake_retries_reasoning_only_once_with_larger_budget():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.gateway.model_gateway import StreamResult
    from app.model_autopilot.probe import probe_model_ping

    catalog = make_catalog(model_id="glm-5.2")
    gateway = AsyncMock(
        side_effect=[
            StreamResult(
                reasoning_text="thinking",
                error="final_content_empty",
                finish_reason="length",
                latency_ms=10,
            ),
            StreamResult(
                final_content="OK",
                finish_reason="stop",
                latency_ms=20,
            ),
        ]
    )
    with patch(
        "app.model_autopilot.probe.stream_completion_and_collect",
        gateway,
    ):
        probe = await probe_model_ping(
            SimpleNamespace(),
            catalog,
            allow_reasoning_retry=True,
        )

    assert probe.status == "ok"
    assert probe.output_valid is True
    assert probe.detail_json["adaptive_retry"] is True
    assert probe.detail_json["first_finish_reason"] == "length"
    assert gateway.await_count == 2
    assert gateway.await_args_list[1].kwargs["max_tokens"] == 2048


@pytest.mark.asyncio
async def test_glm_evidence_gateway_has_room_for_final_content():
    from unittest.mock import AsyncMock, patch

    from app.gateway.model_gateway import StreamResult
    from app.model_eval.engine import _default_gateway

    gateway = AsyncMock(return_value=StreamResult(final_content="OK"))
    with patch(
        "app.gateway.model_gateway.stream_completion_and_collect",
        gateway,
    ):
        await _default_gateway(
            system_prompt="system",
            user_content="user",
            model="glm-5.2",
            temperature=0,
            max_tokens=128,
            provider="new-api",
        )

    assert gateway.await_args.kwargs["max_tokens"] == 2048
    assert gateway.await_args.kwargs["reasoning_mode"] == "disabled"


@pytest.mark.asyncio
async def test_prewrite_handshake_promotes_only_configured_unknown_text_model():
    from unittest.mock import AsyncMock, patch

    from app.model_autopilot.preflight import bootstrap_catalog_and_probes

    db = FakeAsyncSession()
    catalog = make_catalog(model_id="configured-text", kind="unknown", eligible=False)
    catalog.auto_route_enabled = False
    catalog.classification_source = "unknown"
    db._table(ModelCatalog).append(catalog)
    db._table(AgentModelBinding).append(
        AgentModelBinding(
            id=uuid.uuid4(),
            scope_type="global",
            scope_id=None,
            agent_role="draft_writer",
            provider="primary",
            primary_model="configured-text",
            fallback_model=None,
            reasoning_mode="auto",
            version=1,
            updated_by="test",
        )
    )
    now = datetime.now(timezone.utc)
    ping = ModelHealthProbe(
        id=uuid.uuid4(),
        model_catalog_id=catalog.id,
        probe_type="l1_ping",
        status="ok",
        started_at=now,
        completed_at=now,
        latency_ms=10,
        output_valid=True,
    )

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch(
        "app.database.async_session_factory", return_value=SessionContext()
    ), patch(
        "app.model_autopilot.preflight._provider_sync_list",
        AsyncMock(return_value=[]),
    ), patch(
        "app.model_autopilot.preflight.probe_model_ping",
        AsyncMock(return_value=ping),
    ):
        report = await bootstrap_catalog_and_probes()

    assert report["configured_models"] == 1
    assert report["promoted_text"] == 1
    assert report["probed"] == 1
    assert report["errors"] == []
    assert catalog.text_generation_eligible is True
    assert catalog.auto_route_enabled is True
    snapshot = db._table(ModelHealthSnapshot)[0]
    assert snapshot.health_status == "healthy"


@pytest.mark.asyncio
async def test_health_cron_never_probes_non_text_catalogs():
    from unittest.mock import AsyncMock, patch
    from app.model_autopilot.jobs import model_health_probe_tick

    db = FakeAsyncSession()
    db._table(ModelCatalog).append(
        make_catalog(model_id="image-only", kind="image_generation", eligible=False)
    )

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, exc_type, exc, tb):
            return False

    probe = AsyncMock()
    with patch("app.model_autopilot.jobs.async_session_factory", return_value=SessionContext()), \
         patch("app.model_autopilot.jobs.probe_model_ping", probe):
        report = await model_health_probe_tick({})
    assert report["probed"] == 0
    probe.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════
# 7. cancel aborts with no valid evidence (P0-7)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_cancel_aborts_without_evidence():
    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)
    run = make_run(catalog=cat, cancel_requested=True)
    db._table(ModelEvalRun).append(run)
    await db.commit()
    # gateway raises if ever called; prove 0 calls on cancel
    async def boom(*args, **kwargs):
        raise AssertionError("gateway must not be called after cancel")
    r = await run_qualification(db, run, gateway=boom, force=True)
    assert r["status"] == "cancelled"
    assert r["gateway_calls"] == 0


@pytest.mark.asyncio
async def test_cancel_poll_closes_read_transaction_before_slow_gateway():
    class TransactionTrackingSession(FakeAsyncSession):
        def __init__(self):
            super().__init__()
            self.read_transaction_open = False

        async def refresh(self, instance, attribute_names=None):
            self.read_transaction_open = True

        async def commit(self):
            await super().commit()
            self.read_transaction_open = False

        async def rollback(self):
            await super().rollback()
            self.read_transaction_open = False

    db = TransactionTrackingSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)
    run = make_run(catalog=cat)
    db._table(ModelEvalRun).append(run)
    await db.commit()

    async def reasoning_only(**kwargs):
        assert not db.read_transaction_open, (
            "the cancellation poll transaction must be closed before gateway I/O"
        )
        return "", "final_content_empty"

    result = await run_qualification(db, run, gateway=reasoning_only, force=True)

    assert result["status"] == "failed"
    assert result["error"] == "final_content_empty"
    assert result["gateway_calls"] == 1
    assert not db.read_transaction_open


@pytest.mark.asyncio
async def test_cancel_poll_rolls_back_poisoned_session_before_failure_persistence():
    class PoisonedRefreshSession(FakeAsyncSession):
        def __init__(self):
            super().__init__()
            self.pending_rollback = False
            self.rollbacks = 0

        async def refresh(self, instance, attribute_names=None):
            self.pending_rollback = True
            raise RuntimeError("connection terminated by idle transaction timeout")

        async def commit(self):
            if self.pending_rollback:
                raise RuntimeError("PendingRollbackError")
            await super().commit()

        async def rollback(self):
            self.rollbacks += 1
            self.pending_rollback = False
            await super().rollback()

    db = PoisonedRefreshSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)
    run = make_run(catalog=cat)
    db._table(ModelEvalRun).append(run)
    await db.commit()

    async def reasoning_only(**kwargs):
        return "", "final_content_empty"

    result = await run_qualification(db, run, gateway=reasoning_only, force=True)

    assert result["status"] == "failed"
    assert result["error"] == "final_content_empty"
    assert result["gateway_calls"] == 1
    assert db.rollbacks == 1
    assert not db.pending_rollback
    assert run.result_summary["error"] == "final_content_empty"
    await db.commit()  # the caller can continue using the session


# ═══════════════════════════════════════════════════════════════════════
# 8. seed async, idempotent, deterministic; GET is read-only; old v1 not mixed
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_async_seed_deterministic_idempotent():
    db = FakeAsyncSession()
    n1 = await seed_suites(db)
    assert n1 == 7, n1
    assert _suite_id("draft-v2", "4") == _suite_id("draft-v2", "4")
    assert _suite_id("context-v2", "2") == _suite_id("context-v2", "2")
    n2 = await seed_suites(db)
    assert n2 == 0, "seed must be idempotent"
    draft = [s for s in db._table(ModelEvalSuite) if s.suite_key == "draft-v2"]
    assert draft and draft[0].target_role == "draft_writer" and draft[0].mode == "qualification"
    ctx = [s for s in db._table(ModelEvalSuite) if s.suite_key == "context-v2"]
    assert ctx and ctx[0].mode == "context_ladder"
    # Ability contract v4 is independent from the still-valid context v2 bank.
    versions = {s.suite_key: s.version for s in db._table(ModelEvalSuite)}
    assert versions["draft-v2"] == "4"
    assert versions["context-v2"] == "2"


@pytest.mark.asyncio
async def test_ensure_v98_suites_idempotent_and_seed_count():
    db = FakeAsyncSession()
    a = await ensure_v98_suites(db)
    b = await ensure_v98_suites(db)
    assert a == 7 and b == 0


# ═══════════════════════════════════════════════════════════════════════
# 9. endpoint normalization: equivalent URLs same hash; routing change differs;
#    secret never appears in persisted/returned values (P0-3)
# ═══════════════════════════════════════════════════════════════════════


def test_endpoint_normalization_equivalence_and_secret_safety():
    a = normalize_endpoint("https://api.example.com/v1/")
    b = normalize_endpoint("https://api.example.com/v1")
    assert a == b
    ha = compute_endpoint_identity_hash(base_url="https://api.example.com/v1", metadata_json={})
    hb = compute_endpoint_identity_hash(base_url="https://api.example.com/v1/")
    assert ha == hb

    # routing endpoint change → different hash
    hc = compute_endpoint_identity_hash(base_url="https://other.example.com/v1")
    assert hc != ha

    # secret never folded in (different api keys, same base → same hash)
    hk = compute_endpoint_identity_hash(base_url="https://api.example.com/v1", metadata_json={"api_key": "sk-xxxx"})
    assert hk == ha

    # normalization strips query/fragment/userinfo
    nq = normalize_endpoint("https://api.example.com/v1?api_key=secret#frag")
    assert nq["path"] == "/v1"
    assert "secret" not in nq["path"] and nq["host"] == "api.example.com"


def test_refresh_endpoint_identity_preserves_synced_hash_without_env(monkeypatch):
    for key in (
        "PRIMARY_BASE_URL", "FALLBACK_BASE_URL", "NEW_API_BASE_URL",
        "OPENROUTER_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    cat = make_catalog(metadata_json={"endpoint_identity_hash": "a" * 64})
    cat.endpoint_identity_hash = "a" * 64
    assert refresh_endpoint_identity(cat) == "a" * 64
    assert cat.endpoint_identity_hash == "a" * 64


def test_provider_metadata_scrubber_removes_nested_and_legacy_secrets():
    from app.model_autopilot.catalog import _safe_provider_metadata

    cleaned = _safe_provider_metadata(
        {
            "id": "glm-5.2",
            "api_key": "secret-a",
            "nested": {"Authorization": "Bearer secret-b", "safe": 1},
            "items": [{"password": "secret-c"}, {"revision": "r1"}],
        }
    )
    serialized = repr(cleaned)
    assert "secret-a" not in serialized
    assert "secret-b" not in serialized
    assert "secret-c" not in serialized
    assert cleaned["nested"]["safe"] == 1
    assert cleaned["items"][1]["revision"] == "r1"


# ═══════════════════════════════════════════════════════════════════════
# 10. two concurrent identical requests → only one gateway execution
#     (the second reuses or returns in_progress; P0-11)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_concurrent_identical_request_dedup():
    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)
    # first run already 'running' within window
    inflight = make_run(catalog=cat, status="running", started_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    db._table(ModelEvalRun).append(inflight)
    await db.commit()

    gw = CountingGateway(responder=passing_responder())
    second = make_run(catalog=cat)
    db._table(ModelEvalRun).append(second)
    await db.commit()
    r = await run_qualification(db, second, gateway=gw, force=False)
    assert r["status"] == "in_progress"
    assert r["gateway_calls"] == 0
    assert r["reuse_reason"] == "concurrent_run_in_progress"


# ═══════════════════════════════════════════════════════════════════════
# 11. current evidence state: valid / stale / missing without LLM (P0-5)
# ═══════════════════════════════════════════════════════════════════════


def test_current_evidence_state_valid_stale_missing():
    st_valid = describe_ability_evidence(
        current_key="k1", stored_key="k1",
        current_identity="i1", stored_identity="i1",
        current_suite="s1", stored_suite="s1",
        current_rev="v98-ability-2", stored_rev="v98-ability-2",
    )
    assert st_valid.state == "valid"

    st_stale = describe_ability_evidence(
        current_key="k2", stored_key="k1",
        current_identity="i2", stored_identity="i1",
        current_suite="s1", stored_suite="s1",
        current_rev="v98-ability-2", stored_rev="v98-ability-2",
    )
    assert st_stale.state == "stale" and "identity" in st_stale.changed_fields

    st_missing = describe_context_evidence(
        current_key=None, stored_key=None,
        current_identity=None, stored_identity=None,
        current_suite=None, stored_suite=None,
        current_rev=CONTEXT_EVALUATOR_REVISION, stored_rev=None,
    )
    assert st_missing.state == "missing"

    # full service: identical facts → both valid
    ident = model_identity_hash(
        provider="primary", model_id="glm-5.2", model_kind="text_generation",
        endpoint_identity_hash="e1",
    )
    full = current_evidence_state(
        provider="primary", model_id="glm-5.2", model_kind="text_generation",
        endpoint_identity_hash="e1",
        ability_suite_hash="s1", context_suite_hash="c1",
        catalog_ability_evaluation_key=ability_evaluation_key(ident, "s1"),
        catalog_ability_identity_hash=ident,
        catalog_ability_suite_hash="s1",
        catalog_ability_evaluator_revision=ABILITY_EVALUATOR_REVISION,
        catalog_context_evaluation_key=context_evaluation_key(ident, "c1"),
        catalog_context_identity_hash=ident,
        catalog_context_suite_hash="c1",
        catalog_context_evaluator_revision=CONTEXT_EVALUATOR_REVISION,
    )
    assert full["ability"]["state"] == "valid"
    assert full["context"]["state"] == "valid"
    # identity change → stale for both
    ident2 = model_identity_hash(
        provider="primary", model_id="glm-5.2", model_kind="text_generation",
        endpoint_identity_hash="e2",
    )
    full2 = current_evidence_state(
        provider="primary", model_id="glm-5.2", model_kind="text_generation",
        endpoint_identity_hash="e2",
        ability_suite_hash="s1", context_suite_hash="c1",
        catalog_ability_evaluation_key=ability_evaluation_key(ident, "s1"),
        catalog_ability_identity_hash=ident,
        catalog_ability_suite_hash="s1",
        catalog_ability_evaluator_revision=ABILITY_EVALUATOR_REVISION,
        catalog_context_evaluation_key=context_evaluation_key(ident, "c1"),
        catalog_context_identity_hash=ident,
        catalog_context_suite_hash="c1",
        catalog_context_evaluator_revision=CONTEXT_EVALUATOR_REVISION,
    )
    assert full2["ability"]["state"] == "stale"
    assert full2["context"]["state"] == "stale"


# ═══════════════════════════════════════════════════════════════════════
# 12. detect does NOT run benchmark and reports needs_qualification; preflight
#     fail-closed for both ability and context (real router logic)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_detect_does_not_run_quick_benchmark():
    from unittest.mock import AsyncMock, patch, MagicMock
    import inspect
    from app.model_autopilot import autoconfig_job
    from app.model_autopilot.autoconfig_job import run_model_detection

    db = FakeAsyncSession()
    cat = make_catalog(model_id="glm-5.2", ability_evaluation_key=None)
    db._table(ModelCatalog).append(cat)
    run = MagicMock()
    run.id = uuid.uuid4()
    run.status = "queued"; run.phase = "queued"; run.progress = 0
    run.finished_at = None; run.started_at = None
    run.recommendation_json = None; run.eligible_models = 0

    detection_source = inspect.getsource(run_model_detection)
    assert "run_quick_role_benchmark" not in detection_source
    assert "probe_model_performance" not in detection_source
    with patch("app.model_autopilot.preflight._provider_sync_list", AsyncMock(return_value=[])), \
         patch.object(autoconfig_job, "_get_models", AsyncMock(return_value=[cat])), \
         patch.object(autoconfig_job, "ensure_capability_for_catalog", AsyncMock()), \
         patch.object(autoconfig_job, "compute_role_score", AsyncMock()), \
         patch.object(autoconfig_job, "build_recommendation",
                      AsyncMock(return_value=({"roles": {}}, 0))), \
         patch("app.model_autopilot.jobs._probe_due", AsyncMock(return_value=False)):
        await run_model_detection(db, run)
    rec = run.recommendation_json
    assert rec is not None and "needs_qualification" in rec
    assert "glm-5.2" in rec["needs_qualification"]


@pytest.mark.asyncio
async def test_preflight_fail_closed_without_evidence():
    from unittest.mock import AsyncMock, patch
    from app.model_autopilot.preflight import run_model_preflight

    db = FakeAsyncSession()
    cat = make_catalog(model_id="glm-5.2", ability_evaluation_key=None, context_evaluation_key=None,
                       certification_level="none")
    db._table(ModelCatalog).append(cat)
    cap = ModelCapabilityProfile(model_catalog_id=cat.id, effective_context_window=128000)
    db._table(ModelCapabilityProfile).append(cap)

    class FakeBinding:
        routing_policy_id = None; allowed_model_ids = []
        blocked_model_ids = []; manual_primary_locked = False
        primary_model = None; provider = None

    class FakeSession:
        status = "pending"; stop_reason = None; stop_detail = None
        model_preflight_status = None; model_preflight_detail = None
        model_route_plan_id = None; model_routing_policy_version = None

    with patch("app.model_autopilot.preflight.ensure_capability_for_catalog", AsyncMock()), \
         patch("app.model_autopilot.preflight.compute_role_score", AsyncMock()):
        res = await run_model_preflight(db, session=FakeSession(), binding=FakeBinding())
    assert res["status"] == "blocked"
    codes = [b.get("code") for b in res["blockers"]]
    assert "MISSING_ABILITY_EVIDENCE" in codes


@pytest.mark.asyncio
async def test_preflight_fail_closed_for_context_only():
    from unittest.mock import AsyncMock, patch
    from app.model_autopilot.preflight import run_model_preflight

    db = FakeAsyncSession()
    # ability present but context missing → context-required roles blocked
    cat = make_catalog(model_id="glm-5.2", ability_evaluation_key="ak", context_evaluation_key=None,
                       certification_level="role_qualified")
    db._table(ModelCatalog).append(cat)
    cap = ModelCapabilityProfile(model_catalog_id=cat.id, effective_context_window=128000)
    db._table(ModelCapabilityProfile).append(cap)
    source = make_run(catalog=cat)
    db._table(ModelEvalRun).append(source)
    await db.commit()
    await run_qualification(
        db,
        source,
        gateway=CountingGateway(responder=passing_responder()),
        force=True,
    )
    for role_score in db._table(ModelRoleScore):
        role_score.benchmark_passed = True
        role_score.composite_score = 95.0

    class FakeBinding:
        routing_policy_id = None; allowed_model_ids = []
        blocked_model_ids = []; manual_primary_locked = False
        primary_model = None; provider = None

    class FakeSession:
        status = "pending"; stop_reason = None; stop_detail = None
        model_preflight_status = None; model_preflight_detail = None
        model_route_plan_id = None; model_routing_policy_version = None

    with patch("app.model_autopilot.preflight.ensure_capability_for_catalog", AsyncMock()), \
         patch("app.model_autopilot.preflight.compute_role_score", AsyncMock()):
        res = await run_model_preflight(db, session=FakeSession(), binding=FakeBinding())
    assert res["status"] == "blocked"
    codes = [b.get("code") for b in res["blockers"]]
    # ability present but context missing → MISSING_CONTEXT_EVIDENCE fires
    assert "MISSING_CONTEXT_EVIDENCE" in codes


@pytest.mark.asyncio
async def test_preflight_uses_each_roles_binding_and_keeps_bound_primary_eligible():
    from unittest.mock import AsyncMock, patch

    from app.model_autopilot.preflight import run_model_preflight
    from app.model_autopilot.router import RoleRouteResult

    db = FakeAsyncSession()
    book_id = uuid.uuid4()
    deep = make_catalog(model_id="deepseek-v4-flash", provider="deepseek")
    step = make_catalog(model_id="step-3.7-flash", provider="stepfun")
    stale = make_catalog(model_id="glm-5.2", provider="zai")
    db._table(ModelCatalog).extend([deep, step, stale])

    draft_binding = AgentModelBinding(
        id=uuid.uuid4(),
        scope_type="book",
        scope_id=book_id,
        agent_role="draft_writer",
        provider=deep.provider,
        primary_model=deep.model_id,
        reasoning_mode="auto",
        version=1,
        updated_by="test",
        routing_policy_id=None,
        manual_primary_locked=False,
        allowed_model_ids=[str(stale.id)],
        blocked_model_ids=[],
    )
    memory_binding = AgentModelBinding(
        id=uuid.uuid4(),
        scope_type="book",
        scope_id=book_id,
        agent_role="memory_compiler",
        provider=step.provider,
        primary_model=step.model_id,
        reasoning_mode="auto",
        version=1,
        updated_by="test",
        routing_policy_id=None,
        manual_primary_locked=False,
        allowed_model_ids=[str(step.id)],
        blocked_model_ids=[],
    )
    db._table(AgentModelBinding).extend([draft_binding, memory_binding])

    calls = {}

    async def fake_route(_db, **kwargs):
        calls[kwargs["agent_role"]] = kwargs
        return RoleRouteResult(
            assignment={
                "primary": {"provider": "chosen", "model": kwargs["agent_role"]},
                "fallbacks": [],
            },
            blockers=None,
        )

    class FakeSession:
        pass

    session = FakeSession()
    session.id = uuid.uuid4()
    session.book_id = book_id
    session.status = "created"
    session.stop_reason = None
    session.stop_detail = None
    session.model_preflight_status = None
    session.model_preflight_detail = None
    session.model_route_plan_id = None
    session.model_routing_policy_version = None

    with (
        patch("app.model_eval.engine.ensure_v98_suites", AsyncMock()),
        patch("app.model_autopilot.preflight.ensure_capability_for_catalog", AsyncMock()),
        patch("app.model_autopilot.preflight.compute_role_score", AsyncMock()),
        patch("app.model_autopilot.preflight.build_role_route", fake_route),
        patch("app.model_autopilot.preflight.policy_from_db", AsyncMock(return_value={})),
    ):
        result = await run_model_preflight(
            db,
            session=session,
            binding=draft_binding,
        )

    assert result["status"] == "pass"
    assert set(calls["draft_writer"]["allowed_ids"]) == {
        str(stale.id),
        str(deep.id),
    }
    assert calls["memory_compiler"]["allowed_ids"] == [str(step.id)]


@pytest.mark.asyncio
async def test_router_uses_current_evidence_and_fresh_health_only():
    from app.model_autopilot.router import build_role_route, default_policy_for

    db = FakeAsyncSession()
    good = make_catalog(model_id="glm-5.2")
    unrelated_bad = make_catalog(model_id="unqualified-model")
    db._table(ModelCatalog).extend([unrelated_bad, good])
    capability = ModelCapabilityProfile(
        id=uuid.uuid4(),
        model_catalog_id=good.id,
        declared_context_window=128000,
    )
    db._table(ModelCapabilityProfile).append(capability)

    ability_run = make_run(catalog=good)
    db._table(ModelEvalRun).append(ability_run)
    await db.commit()
    await run_qualification(
        db,
        ability_run,
        gateway=CountingGateway(responder=passing_responder()),
        force=True,
    )
    draft_score = next(row for row in db._table(ModelRoleScore) if row.agent_role == "draft_writer")
    draft_score.benchmark_score = 88.3
    draft_score.benchmark_passed = True
    # The operational composite can legitimately be lower than the writing
    # floor before production samples exist.  Current role qualification is
    # the authoritative quality signal and must remain reusable.
    draft_score.composite_score = 77.1

    context_run = make_run(mode="context_ladder", catalog=good)
    db._table(ModelEvalRun).append(context_run)
    await db.commit()
    await run_context_ladder(
        db,
        context_run,
        good,
        gateway=CountingGateway(
            responder=lambda: (
                '{"original_code":"4471","current_code":"8820","source":"documented_reset"}',
                None,
            )
        ),
        force=True,
    )
    snapshot = ModelHealthSnapshot(
        id=uuid.uuid4(),
        model_catalog_id=good.id,
        health_status="healthy",
        health_score=100.0,
        success_rate_15m=1.0,
        last_probe_at=datetime.now(timezone.utc),
    )
    db._table(ModelHealthSnapshot).append(snapshot)

    policy = default_policy_for(require_provider_diversity=False, fallback_count=0)
    route = await build_role_route(
        db,
        agent_role="draft_writer",
        required_context=50000,
        policy=policy,
    )
    assert route.assignment is not None
    assert route.assignment["primary"]["model"] == "glm-5.2"
    assert route.assignment["primary"]["role_quality"] == 88.3
    assert route.assignment["primary"]["route_composite_score"] == 77.1
    assert route.blockers is None  # unrelated bad catalog must not block a valid route

    # A high operational aggregate must never override failed role evidence.
    draft_score.benchmark_passed = False
    draft_score.composite_score = 99.0
    failed_qualification = await build_role_route(
        db,
        agent_role="draft_writer",
        required_context=50000,
        policy=policy,
    )
    assert failed_qualification.assignment is None
    assert any(
        item.get("model") == good.model_id
        and item.get("code") == "ROLE_QUALIFICATION_FAILED"
        for item in failed_qualification.blockers or []
    )
    draft_score.benchmark_passed = True
    draft_score.composite_score = 77.1

    excluded = await build_role_route(
        db,
        agent_role="draft_writer",
        required_context=50000,
        policy=policy,
        blocked_ids=[str(good.id)],
    )
    assert excluded.assignment is None
    assert any(
        item.get("model") == good.model_id
        and item.get("code") == "MODEL_BLOCKED_BY_BINDING"
        for item in excluded.blockers or []
    )

    snapshot.last_probe_at = datetime.now(timezone.utc) - timedelta(minutes=6)
    stale = await build_role_route(
        db,
        agent_role="draft_writer",
        required_context=50000,
        policy=policy,
    )
    assert stale.assignment is None
    assert "MISSING_FRESH_HEALTH" in {item["code"] for item in stale.blockers or []}


# ═══════════════════════════════════════════════════════════════════════
# 13. API force/cache response shape + UI client contract
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_api_force_and_cache_response_shape():
    db = FakeAsyncSession()
    cat = make_catalog()
    db._table(ModelCatalog).append(cat)

    gw = CountingGateway(responder=passing_responder())
    run1 = make_run(catalog=cat)
    db._table(ModelEvalRun).append(run1)
    await db.commit()
    resp = await run_qualification(db, run1, gateway=gw, force=False)
    assert "ability_evaluation_key" in resp
    assert resp["gateway_calls"] > 0
    assert resp.get("reused") is False

    # ordinary second call: reuse, 0 calls, shape complete
    gw2 = CountingGateway()
    run2 = make_run(catalog=cat)
    db._table(ModelEvalRun).append(run2)
    await db.commit()
    resp2 = await run_qualification(db, run2, gateway=gw2, force=False)
    assert resp2.get("reused") is True
    assert resp2["gateway_calls"] == 0
    assert resp2.get("triggered_by") == "cache_hit"
    for k in ("ability_evaluation_key", "reuse_reason", "source_run_id", "triggered_by", "gateway_calls"):
        assert k in resp2

    # context certify shape
    gw3 = CountingGateway(responder=lambda: ("口令是 4471；复位后口令是 8820。", None))
    run3 = make_run(mode="context_ladder", catalog=cat)
    db._table(ModelEvalRun).append(run3)
    await db.commit()
    rctx = await run_context_ladder(db, run3, cat, gateway=gw3, force=False)
    for k in ("context_evaluation_key", "reused", "triggered_by"):
        assert k in rctx


@pytest.mark.asyncio
async def test_api_submit_queues_first_run_and_reuses_current_source_inline():
    from unittest.mock import AsyncMock, patch
    from app.routers.model_setup import _submit_evaluation

    first_db = FakeAsyncSession()
    first_catalog = make_catalog()
    first_db._table(ModelCatalog).append(first_catalog)
    enqueue = AsyncMock()
    with patch("app.routers.model_setup._enqueue_run", enqueue):
        queued = await _submit_evaluation(
            catalog_id=str(first_catalog.id),
            mode="qualification",
            force=False,
            db=first_db,
        )
    assert queued["queued"] is True
    assert queued["status"] == "queued"
    enqueue.assert_awaited_once()

    cached_db = FakeAsyncSession()
    cached_catalog = make_catalog()
    cached_db._table(ModelCatalog).append(cached_catalog)
    source = make_run(catalog=cached_catalog)
    cached_db._table(ModelEvalRun).append(source)
    await cached_db.commit()
    await run_qualification(
        cached_db,
        source,
        gateway=CountingGateway(responder=passing_responder()),
        force=True,
    )
    no_enqueue = AsyncMock()
    with patch("app.routers.model_setup._enqueue_run", no_enqueue):
        reused = await _submit_evaluation(
            catalog_id=str(cached_catalog.id),
            mode="qualification",
            force=False,
            db=cached_db,
        )
    assert reused["queued"] is False
    assert reused["reused"] is True
    assert reused["gateway_calls"] == 0
    no_enqueue.assert_not_awaited()


def test_ui_client_contract():
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api.ts"
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    for contract in ("evidence:", "qualify:", "contextCertify:", "evalRun:", "cancelEvalRun:"):
        assert contract in text
    page = (
        Path(__file__).resolve().parents[2]
        / "frontend" / "src" / "features" / "model-setup" / "ModelSetupPage.tsx"
    ).read_text(encoding="utf-8")
    assert "evidenceRows.map" in page
    assert 'submitEvaluation(m, "qualification", true)' in page
    assert 'submitEvaluation(m, "context_ladder", true)' in page


# ═══════════════════════════════════════════════════════════════════════
# 14. migration ORM parity (offline — no DB): the migration column set for
#     model_catalog / model_eval_runs / model_context_profiles / model_role_scores
#     matches the ORM models' columns. (P0-11; no fake unique claims.)
# ═══════════════════════════════════════════════════════════════════════


def _migration_column_ops(function_name: str, operation: str) -> dict[str, set[str]]:
    """Parse multiline Alembic batch operations without regex blind spots."""
    import ast

    mig_path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0021_v98_model_evidence.py"
    tree = ast.parse(mig_path.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    )
    found: dict[str, set[str]] = {}
    for node in ast.walk(function):
        if not isinstance(node, ast.With) or not node.items:
            continue
        context = node.items[0].context_expr
        if not (
            isinstance(context, ast.Call)
            and isinstance(context.func, ast.Attribute)
            and context.func.attr == "batch_alter_table"
            and context.args
            and isinstance(context.args[0], ast.Constant)
        ):
            continue
        table = str(context.args[0].value)
        for child in ast.walk(node):
            if not (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "batch"
                and child.func.attr == operation
                and child.args
            ):
                continue
            arg = child.args[0]
            if operation == "add_column":
                if not (isinstance(arg, ast.Call) and arg.args and isinstance(arg.args[0], ast.Constant)):
                    continue
                name = str(arg.args[0].value)
            else:
                if not isinstance(arg, ast.Constant):
                    continue
                name = str(arg.value)
            found.setdefault(table, set()).add(name)
    return found


def test_migration_orm_parity_columns():
    from app.models import ModelCatalog, ModelEvalRun, ModelContextProfile, ModelRoleScore

    orm_cols = {
        "model_catalog": {c.name for c in ModelCatalog.__table__.columns},
        "model_eval_runs": {c.name for c in ModelEvalRun.__table__.columns},
        "model_context_profiles": {c.name for c in ModelContextProfile.__table__.columns},
        "model_role_scores": {c.name for c in ModelRoleScore.__table__.columns},
    }

    mig_cols = _migration_column_ops("upgrade", "add_column")
    # every column the migration adds MUST have a real ORM counterpart
    for t, cols in mig_cols.items():
        for name in cols:
            assert name in orm_cols[t], f"migration column {name} has no ORM counterpart in {t} (fake column!)"

    # the v9.8 evidence columns this migration is responsible for must be present
    expected = {
        "model_catalog": {
            "endpoint_identity_hash", "upstream_identity_hash",
            "ability_evaluation_key", "ability_identity_hash", "ability_suite_hash",
            "ability_evaluator_revision", "ability_reuse_reason", "ability_source_run_id",
            "ability_completed_at",
            "context_evaluation_key", "context_identity_hash", "context_suite_hash",
            "context_evaluator_revision", "context_source_run_id", "context_completed_at",
        },
        "model_eval_runs": {
            "ability_evaluation_key", "ability_identity_hash", "ability_suite_hash",
            "ability_evaluator_revision", "ability_source_run_id",
            "context_evaluation_key", "context_identity_hash", "context_suite_hash",
            "context_evaluator_revision", "context_source_run_id",
            "reuse_reason", "triggered_by", "force_requested", "gateway_calls",
        },
        "model_context_profiles": {
            "context_evaluation_key", "context_identity_hash", "context_suite_hash",
            "context_evaluator_revision", "context_source_run_id",
        },
        "model_role_scores": {
            "benchmark_evidence_key", "benchmark_source_run_id", "benchmark_passed",
        },
    }
    for t, cols in expected.items():
        missing = cols - orm_cols[t]
        assert not missing, f"{t} migration columns missing from ORM: {missing}"
        # and those same columns are in the migration source
        assert cols <= mig_cols[t], f"{t} columns declared in test but not in migration source: {cols - mig_cols[t]}"


def test_migration_downgrade_symmetric():
    """Every per-table upgrade column has a matching downgrade operation."""
    added = _migration_column_ops("upgrade", "add_column")
    dropped = _migration_column_ops("downgrade", "drop_column")
    assert added == dropped, f"migration upgrade/downgrade asymmetric: add={added} drop={dropped}"


def test_migration_has_source_lineage_constraints_and_active_claim():
    mig_path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0021_v98_model_evidence.py"
    text = mig_path.read_text(encoding="utf-8")
    assert text.count('ondelete="SET NULL"') == 6
    assert '"uq_model_eval_runs_active_claim"' in text
    assert "status IN ('running', 'in_progress')" in text
    assert "superseded_during_v98_migration" in text


@pytest.mark.asyncio
async def test_get_evaluation_suites_is_read_only():
    """GET /evaluation/suites must not write (P0-10). We verify seed_suites is
    NOT invoked by importing the route and checking it doesn't call seed."""
    from app.routers import model_setup
    import inspect
    src = inspect.getsource(model_setup.eval_suites)
    # the read-only handler must not call seed_suites / db.commit
    assert "seed_suites" not in src
    assert "db.commit" not in src
