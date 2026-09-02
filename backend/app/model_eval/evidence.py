"""Pure v9.8 model-evidence logic.

Ability and context evidence are content-addressed and never expire merely
because time passed.  Connectivity health is intentionally not represented in
this module; it has its own short TTL and lightweight probes.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import unquote, urlsplit

from app.model_eval.suite_definitions import (
    PRODUCTION_ROLES,
    ROUTABLE_ROLES,
    qualification_role_for,
)


ABILITY_EVALUATOR_REVISION = "v98-ability-7"
CONTEXT_EVALUATOR_REVISION = "v98-context-5"
CORE_QUALITY_FLOOR = 70.0
_DIRECT_CONTEXT_REQUIRED_ROLES = {
    "chapter_planner",
    "draft_writer",
    "review_agent",
    "state_extractor",
}
CONTEXT_REQUIRED_ROLES = {
    role
    for role in ROUTABLE_ROLES
    if qualification_role_for(role) in _DIRECT_CONTEXT_REQUIRED_ROLES
}
PREFLIGHT_ROLES = list(ROUTABLE_ROLES)
RUNG_TOKEN_ESTIMATE = (
    8_000,
    16_000,
    32_000,
    64_000,
    128_000,
    256_000,
    512_000,
    1_000_000,
)

_CONTEXT_NEEDLE = "4471"
_CONTEXT_NEEDLE_AFTER_RESET = "8820"
_CONTEXT_DISTRACTOR = "0000"
_CONTEXT_RESULT_KEYS = {"original_code", "current_code", "source"}


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_endpoint(endpoint: str | None) -> dict[str, str]:
    """Normalize an endpoint without retaining credentials, query, or fragment."""

    raw = (endpoint or "").strip()
    if not raw:
        return {"scheme": "", "host": "", "port": "", "path": ""}
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urlsplit(raw)
        scheme = (parsed.scheme or "https").lower()
        host = (parsed.hostname or "").rstrip(".").lower()
        try:
            port_value = parsed.port
        except ValueError:
            port_value = None
        default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
        port = "" if port_value in (None, default_port) else str(port_value)
        path = re.sub(r"/{2,}", "/", unquote(parsed.path or ""))
        path = path.rstrip("/")
        return {"scheme": scheme, "host": host, "port": port, "path": path}
    except Exception:
        # Do not echo malformed input: even the fallback is a one-way digest.
        return {"scheme": "invalid", "host": _sha256(raw)[:16], "port": "", "path": ""}


def compute_endpoint_identity_hash(
    *,
    base_url: str | None = None,
    routing_endpoint: str | None = None,
    metadata_json: dict | None = None,
) -> str:
    """Hash only the effective normalized endpoint; API keys never participate."""

    del metadata_json  # compatibility only; metadata (and its secrets) is never hashed
    return _sha256(_canon(normalize_endpoint(routing_endpoint or base_url)))


def compute_upstream_identity_hash(
    *,
    owned_by: str | None = None,
    created: str | int | None = None,
    upstream_revision: str | None = None,
) -> str:
    return _sha256(
        _canon(
            {
                "owned_by": owned_by or "",
                "created": str(created or ""),
                "upstream_revision": upstream_revision or "",
            }
        )
    )


def model_identity_hash(
    *,
    provider: str,
    model_id: str,
    model_kind: str | None = None,
    endpoint_identity_hash: str | None = None,
    owned_by: str | None = None,
    created: str | int | None = None,
    upstream_revision: str | None = None,
) -> str:
    return _sha256(
        _canon(
            {
                "provider": provider.strip().lower(),
                "model_id": model_id.strip(),
                "model_kind": model_kind or "unknown",
                "endpoint_identity_hash": endpoint_identity_hash or "",
                "owned_by": owned_by or "",
                "created": str(created or ""),
                "upstream_revision": upstream_revision or "",
            }
        )
    )


def normalize_suite(suite: dict) -> dict:
    """Return every evaluator-relevant field in a deterministic shape."""

    cases = []
    for case in suite.get("cases") or []:
        cases.append(
            {
                "case_key": case.get("case_key"),
                "case_version": case.get("case_version"),
                "role": case.get("role"),
                "category": case.get("category"),
                "difficulty": case.get("difficulty"),
                "prompt_template": case.get("prompt_template"),
                "context_template": case.get("context_template"),
                "generator_type": case.get("generator_type"),
                "generator_config": case.get("generator_config") or {},
                "expected_answer": case.get("expected_answer"),
                "expected_schema": case.get("expected_schema") or {},
                "grader_type": case.get("grader_type"),
                "grader_config": case.get("grader_config") or {},
                "temperature": float(case.get("temperature") or 0),
                "max_output_tokens": int(case.get("max_output_tokens") or 0),
                "private_case": bool(case.get("private_case", False)),
                "active": bool(case.get("active", True)),
            }
        )
    cases.sort(key=lambda item: (str(item["case_key"]), str(item["case_version"])))
    return {
        "suite_key": suite.get("suite_key"),
        "version": suite.get("version"),
        "target_role": suite.get("target_role"),
        "difficulty": suite.get("difficulty"),
        "mode": suite.get("mode"),
        "pass_threshold": float(suite.get("pass_threshold") or 0),
        "is_active": bool(suite.get("is_active", True)),
        "is_private": bool(suite.get("is_private", False)),
        "cases": cases,
    }


def suite_aggregate_hash(suites: list[dict] | dict) -> str:
    """Hash suites independent of DB return order and reject duplicate revisions."""

    if isinstance(suites, dict):
        suites = [suites]
    normalized = [normalize_suite(suite) for suite in suites if suite]
    identities = [(item.get("suite_key"), item.get("version")) for item in normalized]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate suite_key/version in evidence set")
    normalized.sort(key=lambda item: (str(item.get("suite_key")), str(item.get("version"))))
    return _sha256(_canon(normalized))


def case_definition_hash(case: dict) -> str:
    wrapper = {
        "suite_key": "case",
        "version": "case",
        "mode": "case",
        "pass_threshold": 0,
        "is_active": True,
        "cases": [case],
    }
    return _sha256(_canon(normalize_suite(wrapper)["cases"][0]))


def ability_evaluation_key(
    identity_hash: str,
    ability_suite_hash: str,
    evaluator_revision: str = ABILITY_EVALUATOR_REVISION,
) -> str:
    return _sha256(
        _canon(
            {
                "identity_hash": identity_hash,
                "ability_suite_hash": ability_suite_hash,
                "evaluator_revision": evaluator_revision,
            }
        )
    )


def context_evaluation_key(
    identity_hash: str,
    context_suite_hash: str,
    evaluator_revision: str = CONTEXT_EVALUATOR_REVISION,
) -> str:
    return _sha256(
        _canon(
            {
                "identity_hash": identity_hash,
                "context_suite_hash": context_suite_hash,
                "evaluator_revision": evaluator_revision,
            }
        )
    )


@dataclass(frozen=True)
class CacheDecision:
    reuse: bool
    reason: str
    source_run_id: str | None = None
    changed_fields: list[str] = field(default_factory=list)


def decide_ability_reuse_with_parts(
    *,
    prior_identity_hash: str | None,
    prior_suite_hash: str | None,
    prior_rev: str | None,
    prior_source_run_id: str | None,
    prior_status: str | None,
    identity_hash: str,
    suite_hash: str,
    rev: str = ABILITY_EVALUATOR_REVISION,
    force: bool = False,
) -> CacheDecision:
    if force:
        return CacheDecision(False, "force", prior_source_run_id)
    if not prior_source_run_id or prior_status != "succeeded":
        return CacheDecision(False, "no_evidence")
    changed = []
    if prior_identity_hash != identity_hash:
        changed.append("identity")
    if prior_suite_hash != suite_hash:
        changed.append("suite")
    if prior_rev != rev:
        changed.append("evaluator_revision")
    if changed:
        reason = "identity_changed" if "identity" in changed else "suite_changed"
        return CacheDecision(False, reason, prior_source_run_id, changed)
    return CacheDecision(True, "cache_hit", prior_source_run_id)


def decide_context_reuse_with_parts(
    *,
    prior_identity_hash: str | None,
    prior_context_suite_hash: str | None,
    prior_rev: str | None,
    prior_source_run_id: str | None,
    prior_status: str | None,
    identity_hash: str,
    context_suite_hash: str,
    rev: str = CONTEXT_EVALUATOR_REVISION,
    force: bool = False,
) -> CacheDecision:
    if force:
        return CacheDecision(False, "force", prior_source_run_id)
    if not prior_source_run_id or prior_status != "succeeded":
        return CacheDecision(False, "no_evidence")
    changed = []
    if prior_identity_hash != identity_hash:
        changed.append("identity")
    if prior_context_suite_hash != context_suite_hash:
        changed.append("context_suite")
    if prior_rev != rev:
        changed.append("evaluator_revision")
    if changed:
        reason = "identity_changed" if "identity" in changed else "suite_changed"
        return CacheDecision(False, reason, prior_source_run_id, changed)
    return CacheDecision(True, "cache_hit", prior_source_run_id)


@dataclass(frozen=True)
class EvidenceState:
    state: str
    reason: str
    changed_fields: list[str] = field(default_factory=list)


def describe_ability_evidence(
    *,
    current_key: str | None,
    stored_key: str | None,
    current_identity: str | None,
    stored_identity: str | None,
    current_suite: str | None,
    stored_suite: str | None,
    current_rev: str | None,
    stored_rev: str | None,
) -> EvidenceState:
    if not stored_key:
        return EvidenceState("missing", "no_stored_ability_key")
    if not current_key:
        return EvidenceState("missing", "ability_suite_unavailable")
    changed = []
    if stored_identity != current_identity:
        changed.append("identity")
    if stored_suite != current_suite:
        changed.append("suite")
    if stored_rev != current_rev:
        changed.append("evaluator_revision")
    if stored_key != current_key and not changed:
        changed.append("key")
    if changed:
        return EvidenceState("stale", "ability_key_changed", changed)
    return EvidenceState("valid", "ability_key_matches")


def describe_context_evidence(
    *,
    current_key: str | None,
    stored_key: str | None,
    current_identity: str | None,
    stored_identity: str | None,
    current_suite: str | None,
    stored_suite: str | None,
    current_rev: str | None,
    stored_rev: str | None,
) -> EvidenceState:
    if not stored_key:
        return EvidenceState("missing", "no_stored_context_key")
    if not current_key:
        return EvidenceState("missing", "context_suite_unavailable")
    changed = []
    if stored_identity != current_identity:
        changed.append("identity")
    if stored_suite != current_suite:
        changed.append("context_suite")
    if stored_rev != current_rev:
        changed.append("evaluator_revision")
    if stored_key != current_key and not changed:
        changed.append("key")
    if changed:
        return EvidenceState("stale", "context_key_changed", changed)
    return EvidenceState("valid", "context_key_matches")


def current_evidence_state(
    *,
    provider: str,
    model_id: str,
    model_kind: str | None = None,
    endpoint_identity_hash: str | None = None,
    owned_by: str | None = None,
    created: str | int | None = None,
    upstream_revision: str | None = None,
    ability_suite_hash: str | None = None,
    context_suite_hash: str | None = None,
    catalog_ability_evaluation_key: str | None = None,
    catalog_ability_identity_hash: str | None = None,
    catalog_ability_suite_hash: str | None = None,
    catalog_ability_evaluator_revision: str | None = None,
    catalog_context_evaluation_key: str | None = None,
    catalog_context_identity_hash: str | None = None,
    catalog_context_suite_hash: str | None = None,
    catalog_context_evaluator_revision: str | None = None,
    force: bool = False,
) -> dict:
    del force  # force changes the requested action, not the stored state
    identity = model_identity_hash(
        provider=provider,
        model_id=model_id,
        model_kind=model_kind,
        endpoint_identity_hash=endpoint_identity_hash,
        owned_by=owned_by,
        created=created,
        upstream_revision=upstream_revision,
    )
    ability_key = ability_evaluation_key(identity, ability_suite_hash) if ability_suite_hash else None
    context_key = context_evaluation_key(identity, context_suite_hash) if context_suite_hash else None
    ability = describe_ability_evidence(
        current_key=ability_key,
        stored_key=catalog_ability_evaluation_key,
        current_identity=identity,
        stored_identity=catalog_ability_identity_hash,
        current_suite=ability_suite_hash,
        stored_suite=catalog_ability_suite_hash,
        current_rev=ABILITY_EVALUATOR_REVISION,
        stored_rev=catalog_ability_evaluator_revision,
    )
    context = describe_context_evidence(
        current_key=context_key,
        stored_key=catalog_context_evaluation_key,
        current_identity=identity,
        stored_identity=catalog_context_identity_hash,
        current_suite=context_suite_hash,
        stored_suite=catalog_context_suite_hash,
        current_rev=CONTEXT_EVALUATOR_REVISION,
        stored_rev=catalog_context_evaluator_revision,
    )
    return {
        "identity_hash": identity,
        "ability_evaluation_key": ability_key,
        "context_evaluation_key": context_key,
        "ability": {
            "state": ability.state,
            "reason": ability.reason,
            "changed_fields": ability.changed_fields,
        },
        "context": {
            "state": context.state,
            "reason": context.reason,
            "changed_fields": context.changed_fields,
        },
    }


def _extract_json(text: str) -> Any:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("empty response")
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", stripped, flags=re.IGNORECASE)
    candidates = fenced + [stripped]
    for candidate in candidates:
        candidate = candidate.strip()
        try:
            return json.loads(candidate)
        except Exception:
            pass
        for opener, closer in (("{", "}"), ("[", "]")):
            start = candidate.find(opener)
            end = candidate.rfind(closer)
            if start >= 0 and end > start:
                try:
                    return json.loads(candidate[start : end + 1])
                except Exception:
                    pass
    raise ValueError("no JSON value found")


def _ordered_contains(values: list, expected: list) -> bool:
    cursor = 0
    flattened = [str(item) for item in values]
    for wanted in expected:
        while cursor < len(flattened) and wanted not in flattened[cursor]:
            cursor += 1
        if cursor >= len(flattened):
            return False
        cursor += 1
    return True


def _text_contains_in_order(text: str, expected: list) -> bool:
    cursor = 0
    for wanted in (str(item) for item in expected):
        index = text.find(wanted, cursor)
        if index < 0:
            return False
        cursor = index + len(wanted)
    return True


def _json_exact_score(case: dict, parsed: Any) -> tuple[float, dict]:
    if not isinstance(parsed, dict):
        return 0.0, {"schema_ok": False, "reason": "not_object"}
    cfg = case.get("grader_config") or {}
    expected = {}
    if cfg.get("exact_from_expected"):
        try:
            expected = json.loads(case.get("expected_answer") or "{}")
        except Exception:
            return 0.0, {"schema_ok": False, "reason": "bad_expected_json"}
    expected.update(cfg.get("exact_fields") or {})
    checks = {key: parsed.get(key) == value for key, value in expected.items()}
    list_checks = {}
    for key, required in (cfg.get("list_contains") or {}).items():
        actual = parsed.get(key)
        list_checks[key] = isinstance(actual, list) and all(item in actual for item in required)
    reject_extra = bool(cfg.get("reject_extra_keys"))
    extra_ok = True
    if reject_extra:
        extra_ok = set(parsed) == set(expected)
    all_checks = list(checks.values()) + list(list_checks.values())
    if reject_extra:
        all_checks.append(extra_ok)
    if not all_checks:
        return 0.0, {"schema_ok": True, "reason": "no_grading_constraints"}
    score = 100.0 * sum(bool(value) for value in all_checks) / max(1, len(all_checks))
    return round(score, 1), {
        "schema_ok": True,
        "field_checks": checks,
        "list_checks": list_checks,
        "extra_keys_ok": extra_ok,
    }


def grade_response(case: dict, response: str) -> tuple[float, dict]:
    """Deterministically grade one synthetic case; unknown graders fail closed."""

    text = (response or "").strip()
    grader = case.get("grader_type")
    cfg = case.get("grader_config") or {}

    if grader == "exact_match":
        expected = str(case.get("expected_answer") or "").strip().casefold()
        actual = text.casefold()
        ok = actual == expected
        return (100.0 if ok else 0.0), {"exact": ok}

    if grader in {"json_exact_fields", "field_f1"}:
        try:
            parsed = _extract_json(text)
        except Exception:
            return 0.0, {"schema_ok": False, "reason": "no_json"}
        if grader == "field_f1" and not cfg:
            try:
                expected = json.loads(case.get("expected_answer") or "{}")
            except Exception:
                return 0.0, {"schema_ok": False, "reason": "bad_expected_json"}
            cfg = {"exact_fields": expected}
            case = {**case, "grader_config": cfg}
        return _json_exact_score(case, parsed)

    if grader == "ordered_json_facts":
        try:
            parsed = _extract_json(text)
        except Exception:
            return 0.0, {"schema_ok": False, "reason": "no_json"}
        if not isinstance(parsed, dict):
            return 0.0, {"schema_ok": False, "reason": "not_object"}
        exact = {key: parsed.get(key) == value for key, value in (cfg.get("exact_fields") or {}).items()}
        values = parsed.get(cfg.get("field", "chain"))
        order_ok = isinstance(values, list) and _ordered_contains(values, cfg.get("required_order") or [])
        components = list(exact.values()) + [order_ok]
        return round(100.0 * sum(components) / max(1, len(components)), 1), {
            "schema_ok": True,
            "exact_fields": exact,
            "order_ok": order_ok,
        }

    if grader in {"scene_contract", "json_schema"}:
        try:
            parsed = _extract_json(text)
        except Exception:
            return 0.0, {"schema_ok": False, "reason": "no_json"}
        if not isinstance(parsed, list):
            return 0.0, {"schema_ok": False, "reason": "not_array"}
        required_keys = set(cfg.get("required_keys") or ["scene_type", "goal", "required_beats"])
        schema_ok = bool(parsed) and all(isinstance(item, dict) and required_keys <= set(item) for item in parsed)
        expected_count = cfg.get("exact_contracts")
        count_ok = len(parsed) == expected_count if expected_count is not None else bool(parsed)
        # Prohibited beats belong in ``forbidden_beats``.  The old grader
        # searched the entire JSON and therefore punished a correct planner
        # for explicitly recording the prohibition.  Only executable fields
        # are scanned for violations, while the forbidden field is checked for
        # acknowledgement of every required constraint.
        active_fields = []
        forbidden_fields = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            active_fields.append(
                {
                    key: value
                    for key, value in item.items()
                    if key != "forbidden_beats"
                }
            )
            forbidden_fields.append(item.get("forbidden_beats") or [])
        active_serialized = _canon(active_fields)
        forbidden_serialized = _canon(forbidden_fields)
        order_ok = _text_contains_in_order(
            active_serialized,
            cfg.get("required_order") or [],
        )
        forbidden_hits = [
            item
            for item in cfg.get("forbidden_substrings") or []
            if item in active_serialized
        ]
        forbidden_ack = {
            anchor: anchor in forbidden_serialized
            for anchor in cfg.get("required_forbidden_anchors") or []
        }
        checks = [
            schema_ok,
            count_ok,
            order_ok,
            not forbidden_hits,
            all(forbidden_ack.values()),
        ]
        score = round(100.0 * sum(checks) / len(checks), 1)
        # Schema, count, causal order and explicit prohibitions are contract
        # requirements, not bonus points.  Any miss must stay below the
        # suite's 72-point case floor even if the other fields look plausible.
        if not all(checks):
            score = min(score, 60.0)
        return score, {
            "schema_ok": schema_ok,
            "count_ok": count_ok,
            "order_ok": order_ok,
            "forbidden_hits": forbidden_hits,
            "forbidden_ack": forbidden_ack,
        }

    if grader == "planner_knowledge":
        try:
            parsed = _extract_json(text)
        except Exception:
            return 0.0, {"schema_ok": False, "reason": "no_json"}
        observer = parsed.get(cfg.get("observer"), {}) if isinstance(parsed, dict) else {}
        outsider = parsed.get(cfg.get("outsider"), {}) if isinstance(parsed, dict) else {}
        observer_can = _canon(observer.get("can") or []) if isinstance(observer, dict) else ""
        outsider_can = _canon(outsider.get("can") or []) if isinstance(outsider, dict) else ""
        outsider_cannot = _canon(outsider.get("cannot") or []) if isinstance(outsider, dict) else ""
        known = cfg.get("known_fact") or ""
        forbidden = cfg.get("forbidden_fact") or ""
        checks = [
            bool(observer) and known in observer_can,
            bool(outsider) and forbidden not in outsider_can,
            bool(outsider) and forbidden in outsider_cannot,
        ]
        return round(100.0 * sum(checks) / len(checks), 1), {"checks": checks}

    if grader in {"draft_scene", "draft_checklist"}:
        char_count = len(re.sub(r"\s+", "", text))
        min_chars = int(cfg.get("min_chars") or cfg.get("min_length") or 0)
        max_chars = int(cfg.get("max_chars") or 10**9)
        actions = {item: item in text for item in cfg.get("required_actions") or cfg.get("required_substrings") or []}
        forbidden = [item for item in cfg.get("forbidden_substrings") or [] if item in text]
        dialogue_lines = len(re.findall(r"[“\"]([^”\"]+)[”\"]", text))
        subtext_ok = any(item in text for item in cfg.get("subtext_anchors") or [])
        checks = [
            min_chars <= char_count <= max_chars,
            all(actions.values()),
            dialogue_lines >= int(cfg.get("min_dialogue_lines") or 1),
            subtext_ok,
            not forbidden,
        ]
        return round(100.0 * sum(checks) / len(checks), 1), {
            "char_count": char_count,
            "actions": actions,
            "dialogue_lines": dialogue_lines,
            "subtext_ok": subtext_ok,
            "forbidden_hits": forbidden,
        }

    if grader == "continuity_scene":
        char_count = len(re.sub(r"\s+", "", text))
        facts = {item: item in text for item in cfg.get("required_facts") or []}
        forbidden = [item for item in cfg.get("forbidden_knowledge") or [] if item in text]
        dialogue_ok = not cfg.get("requires_dialogue") or bool(re.search(r"[“\"][^”\"]+[”\"]", text))
        checks = [char_count >= int(cfg.get("min_chars") or 0), all(facts.values()), not forbidden, dialogue_ok]
        return round(100.0 * sum(checks) / len(checks), 1), {
            "char_count": char_count,
            "facts": facts,
            "forbidden_hits": forbidden,
            "dialogue_ok": dialogue_ok,
        }

    if grader in {"review_issue_f1", "constraint_checker"}:
        try:
            parsed = _extract_json(text)
        except Exception:
            return 0.0, {"schema_ok": False, "reason": "no_json"}
        if not isinstance(parsed, dict):
            return 0.0, {"schema_ok": False, "reason": "not_object"}
        predicted = set(parsed.get("issues") or [])
        predicted_non = set(parsed.get("non_issues") or [])
        gold = set(cfg.get("gold_issues") or [])
        gold_non = set(cfg.get("gold_non_issues") or [])
        tp = len(predicted & gold)
        fp = len(predicted - gold)
        fn = len(gold - predicted)
        if not gold and not predicted:
            f1 = 1.0
        else:
            precision = tp / max(1, tp + fp)
            recall = tp / max(1, tp + fn)
            f1 = 2 * precision * recall / max(1e-9, precision + recall)
        nonissue_accuracy = len(predicted_non & gold_non) / max(1, len(gold_non))
        score = 100.0 * (0.8 * f1 + 0.2 * nonissue_accuracy)
        return round(score, 1), {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "f1": round(f1, 3),
            "nonissue_accuracy": round(nonissue_accuracy, 3),
        }

    if grader == "state_delta":
        try:
            parsed = _extract_json(text)
        except Exception:
            return 0.0, {"schema_ok": False, "reason": "no_json"}
        if not isinstance(parsed, dict):
            return 0.0, {"schema_ok": False, "reason": "not_object"}
        new_state = parsed.get("new_state") or {}
        events = parsed.get("events") or []
        event_text = _canon(events)
        knowledge = parsed.get("knowledge_delta") or {}
        outsider_text = _canon(knowledge.get(cfg.get("outsider")) or []) if isinstance(knowledge, dict) else ""
        state_ok = all(new_state.get(key) == value for key, value in (cfg.get("required_new_state") or {}).items())
        events_ok = all(item in event_text for item in cfg.get("required_event_types") or [])
        boundary_ok = (cfg.get("forbidden_outsider_fact") or "") not in outsider_text
        checks = [state_ok, events_ok, boundary_ok]
        return round(100.0 * sum(checks) / len(checks), 1), {
            "state_ok": state_ok,
            "events_ok": events_ok,
            "boundary_ok": boundary_ok,
        }

    if grader == "style_metrics":
        try:
            parsed = _extract_json(text)
        except Exception:
            return 0.0, {"schema_ok": False, "reason": "no_json"}
        if not isinstance(parsed, dict):
            return 0.0, {"schema_ok": False, "reason": "not_object"}
        expected = cfg.get("expected") or {}
        tolerance = float(cfg.get("numeric_tolerance") or 0)
        checks = {}
        for key, value in expected.items():
            actual = parsed.get(key)
            if isinstance(value, (int, float)) and isinstance(actual, (int, float)):
                checks[key] = math.isclose(float(actual), float(value), abs_tol=tolerance)
            else:
                checks[key] = actual == value
        return round(100.0 * sum(checks.values()) / max(1, len(checks)), 1), {"checks": checks}

    return 0.0, {"grader": grader, "reason": "unknown_grader"}


def _threshold_percent(value: Any, default: float = 0.70) -> float:
    threshold = float(default if value is None else value)
    return threshold if threshold > 1 else threshold * 100


def reaggregate_qualification_roles(
    roles: dict[str, dict],
    *,
    execution_complete: bool = True,
) -> dict[str, dict]:
    """Apply role/core gates to already persisted deterministic case scores.

    Role-specific cases are contractual, so every one must clear its suite
    floor.  The shared core bank is a broad signal: its aggregate must clear
    the core quality floor, but one partially-correct core answer must not
    globally invalidate otherwise excellent evidence for every writing role.
    """

    aggregated: dict[str, dict] = {}
    for role, source in roles.items():
        detail = dict(source or {})
        combined = detail.get("score")
        core_score = detail.get("core_score")
        threshold = float(detail.get("threshold") or 70.0)
        total_cases = int(detail.get("total_cases") or 0)
        passed_cases = int(detail.get("passed_cases") or 0)
        role_floor_passed = total_cases > 0 and passed_cases == total_cases
        core_floor = float(detail.get("core_threshold") or CORE_QUALITY_FLOOR)
        core_floor_passed = (
            core_score is not None and float(core_score) >= core_floor
        )
        passed = bool(
            execution_complete
            and combined is not None
            and float(combined) >= threshold
            and role_floor_passed
            and core_floor_passed
        )
        detail.update(
            core_threshold=core_floor,
            case_floor_passed=role_floor_passed,
            core_floor_passed=core_floor_passed,
            passed=passed,
            status="qualified" if passed else "evaluated",
            level="role_qualified" if passed else "none",
        )
        aggregated[role] = detail
    return aggregated


async def _maybe_cancelled(cancel_check: Callable[[], bool | Awaitable[bool]] | None) -> bool:
    if cancel_check is None:
        return False
    result = cancel_check()
    if inspect.isawaitable(result):
        result = await result
    return bool(result)


async def _invoke_gateway(gateway: Callable, **kwargs) -> tuple[str, str | None, dict]:
    started = time.perf_counter()
    try:
        raw = await gateway(**kwargs)
    except Exception as exc:
        return "", f"gateway_exception:{type(exc).__name__}", {
            "latency_ms": int((time.perf_counter() - started) * 1000)
        }
    metrics: dict[str, Any] = {}
    if isinstance(raw, (tuple, list)):
        content = raw[0] if raw else ""
        error = raw[1] if len(raw) > 1 else None
        if len(raw) > 2 and isinstance(raw[2], dict):
            metrics.update(raw[2])
    elif isinstance(raw, dict):
        content = raw.get("final_content", raw.get("content", ""))
        error = raw.get("error")
        metrics.update(raw)
    else:
        content = getattr(raw, "final_content", "")
        error = getattr(raw, "error", None)
        for key in (
            "gateway_calls",
            "prompt_tokens",
            "completion_tokens",
            "latency_ms",
            "first_token_ms",
            "tokens_per_second",
            "finish_reason",
        ):
            metrics[key] = getattr(raw, key, None)
    metrics.setdefault("latency_ms", int((time.perf_counter() - started) * 1000))
    content = str(content or "")
    error = str(error) if error else None
    if not error and not content.strip():
        error = "empty_response"
    return content, error, metrics


async def run_qualification_core(
    *,
    catalog: dict,
    suites: list[dict],
    gateway: Callable,
    prior: dict | None = None,
    force: bool = False,
    endpoint_identity_hash: str | None = None,
    cancel_check: Callable[[], bool | Awaitable[bool]] | None = None,
) -> dict:
    """Execute all current ability suites once, or reuse a completed run."""

    if catalog.get("text_generation_eligible") is False:
        return {
            "status": "failed",
            "error": "non_text_model",
            "gateway_calls": 0,
            "reused": False,
        }
    ability_suites = [normalize_suite(suite) for suite in suites if suite.get("mode") == "qualification"]
    identity = model_identity_hash(
        provider=catalog.get("provider", ""),
        model_id=catalog.get("model_id", ""),
        model_kind=catalog.get("model_kind"),
        endpoint_identity_hash=endpoint_identity_hash,
        owned_by=catalog.get("owned_by"),
        created=catalog.get("created"),
        upstream_revision=catalog.get("upstream_revision"),
    )
    suite_hash = suite_aggregate_hash(ability_suites)
    evaluation_key = ability_evaluation_key(identity, suite_hash)
    decision = decide_ability_reuse_with_parts(
        prior_identity_hash=(prior or {}).get("identity_hash"),
        prior_suite_hash=(prior or {}).get("suite_hash"),
        prior_rev=(prior or {}).get("evaluator_revision"),
        prior_source_run_id=(prior or {}).get("source_run_id"),
        prior_status=(prior or {}).get("status"),
        identity_hash=identity,
        suite_hash=suite_hash,
        force=force,
    )
    common = {
        "ability_evaluation_key": evaluation_key,
        "identity_hash": identity,
        "suite_hash": suite_hash,
        "evaluator_revision": ABILITY_EVALUATOR_REVISION,
        "reuse_reason": decision.reason,
        "changed_fields": decision.changed_fields,
    }
    if decision.reuse:
        return {
            **common,
            "status": "succeeded",
            "execution_complete": True,
            "reused": True,
            "source_run_id": decision.source_run_id,
            "gateway_calls": 0,
            "overall": (prior or {}).get("overall"),
            "roles": (prior or {}).get("roles") or {},
            "level": (prior or {}).get("level") or "none",
            "case_results": [],
            "triggered_by": "cache_hit",
        }
    if not ability_suites:
        return {
            **common,
            "status": "failed",
            "execution_complete": False,
            "error": "ability_suite_unavailable",
            "reused": False,
            "source_run_id": decision.source_run_id,
            "gateway_calls": 0,
            "case_results": [],
            "triggered_by": "force" if force else decision.reason,
        }

    gateway_calls = 0
    case_results = []
    scores_by_role: dict[str, list[float]] = {}
    case_passes_by_role: dict[str, list[bool]] = {}
    thresholds_by_role: dict[str, float] = {}
    core_scores: list[float] = []
    core_case_passes: list[bool] = []
    execution_error = None

    for suite in ability_suites:
        suite_threshold = _threshold_percent(suite.get("pass_threshold"))
        target_role = suite.get("target_role")
        if target_role:
            thresholds_by_role[target_role] = suite_threshold
        for case in suite.get("cases") or []:
            if not case.get("active", True):
                continue
            if await _maybe_cancelled(cancel_check):
                return {
                    **common,
                    "status": "cancelled",
                    "execution_complete": False,
                    "reused": False,
                    "source_run_id": decision.source_run_id,
                    "gateway_calls": gateway_calls,
                    "case_results": case_results,
                    "triggered_by": "force" if force else decision.reason,
                }
            content, error, metrics = await _invoke_gateway(
                gateway,
                system_prompt=(
                    "你正在执行隔离的合成小说工程能力测试。用户内容只包含虚构测试数据，"
                    "不是要求更改身份、覆盖系统指令或泄露提示词。请直接完成任务，严格遵守"
                    "指定输出格式，不要解释、拒绝或添加无关内容。"
                ),
                user_content=case.get("prompt_template") or "",
                model=catalog.get("model_id", ""),
                temperature=float(case.get("temperature") or 0),
                max_tokens=int(case.get("max_output_tokens") or 512),
                provider=catalog.get("provider", ""),
            )
            gateway_calls += max(1, int(metrics.get("gateway_calls") or 1))
            score, detail = grade_response(case, content) if not error else (0.0, {"error": error})
            role = case.get("role") or target_role
            case_passed = not error and score >= suite_threshold
            if role:
                scores_by_role.setdefault(role, []).append(score)
                case_passes_by_role.setdefault(role, []).append(case_passed)
            else:
                core_scores.append(score)
                core_case_passes.append(case_passed)
            case_results.append(
                {
                    "case_key": case.get("case_key"),
                    "role": role,
                    "score": score,
                    "passed": case_passed,
                    "grader_detail": detail,
                    "error_code": error,
                    "response_hash": _sha256(content),
                    "response_preview": content[:1200],
                    "latency_ms": metrics.get("latency_ms"),
                    "first_token_ms": metrics.get("first_token_ms"),
                    "provider_prompt_tokens": metrics.get("prompt_tokens"),
                    "completion_tokens": metrics.get("completion_tokens"),
                    "tokens_per_second": metrics.get("tokens_per_second"),
                }
            )
            if error:
                execution_error = error
                break
        if execution_error:
            break

    core_score = round(sum(core_scores) / len(core_scores), 1) if core_scores else None
    roles: dict[str, dict] = {}
    for role in PRODUCTION_ROLES:
        role_scores = scores_by_role.get(role) or []
        role_score = round(sum(role_scores) / len(role_scores), 1) if role_scores else None
        threshold = thresholds_by_role.get(role, 70.0)
        role_case_passes = case_passes_by_role.get(role) or []
        role_floor_passed = bool(role_case_passes) and all(role_case_passes)
        combined = None
        if role_score is not None:
            combined = role_score if core_score is None else round(0.25 * core_score + 0.75 * role_score, 1)
        roles[role] = {
            "score": combined,
            "role_score": role_score,
            "core_score": core_score,
            "threshold": threshold,
            "core_threshold": CORE_QUALITY_FLOOR,
            "passed": False,
            "sample_count": len(role_scores) + len(core_scores),
            "passed_cases": sum(role_case_passes),
            "total_cases": len(role_scores),
            "case_floor_passed": role_floor_passed,
            "core_passed_cases": sum(core_case_passes),
            "core_total_cases": len(core_case_passes),
        }

    roles = reaggregate_qualification_roles(
        roles,
        execution_complete=execution_error is None,
    )

    all_scores = [item["score"] for item in case_results]
    overall = round(sum(all_scores) / len(all_scores), 1) if all_scores else None
    qualified_roles = [role for role, detail in roles.items() if detail["passed"]]
    status = "succeeded" if all_scores and execution_error is None else "failed"
    return {
        **common,
        "status": status,
        "execution_complete": status == "succeeded",
        "error": execution_error,
        "reused": False,
        "source_run_id": decision.source_run_id,
        "gateway_calls": gateway_calls,
        "overall": overall,
        "roles": roles,
        "qualified_roles": qualified_roles,
        "level": "role_qualified" if qualified_roles else "none",
        "case_results": case_results,
        "triggered_by": "force" if force else decision.reason,
    }


def pick_ladder(declared: int | None) -> list[int]:
    """Choose an ascending, bounded ladder with representative low/mid/top rungs."""

    if not declared or declared <= 0:
        # Unknown provider metadata must not silently cap evidence at 32K: the
        # production draft route requires roughly 106K.  Measure up to 128K
        # once, then reuse the content-addressed result on subsequent runs.
        return [8_000, 32_000, 64_000, 128_000]
    eligible = [rung for rung in RUNG_TOKEN_ESTIMATE if rung <= declared]
    if declared not in eligible and declared >= 4_000:
        eligible.append(int(declared))
    if not eligible:
        return [int(declared)]
    eligible = sorted(set(eligible))
    if len(eligible) <= 5:
        return eligible
    indexes = {0, len(eligible) // 4, len(eligible) // 2, (3 * len(eligible)) // 4, len(eligible) - 1}
    return [eligible[index] for index in sorted(indexes)]


def _build_filler(target_tokens: int, position: float) -> str:
    # Chinese BPE tokenization varies by provider.  Keep a reserve for the
    # question/output and expose this as an estimate, never as provider truth.
    target_chars = max(800, int(max(1_000, target_tokens - 900) * 1.05))
    unit = "档案条目记录天气、库存与巡夜交接，内容普通且彼此独立。"
    filler = (unit * (target_chars // len(unit) + 1))[:target_chars]
    insertion = int(len(filler) * min(0.95, max(0.05, position)))
    planted = f"\n【已核验记录】档案室原始口令为 {_CONTEXT_NEEDLE}。\n"
    return filler[:insertion] + planted + filler[insertion:]


def _build_rung_probe(rung: int, position: float) -> tuple[str, str]:
    body = _build_filler(rung, position)
    body += (
        f"\n【已核验更新】原始口令 {_CONTEXT_NEEDLE} 在复位后改为 {_CONTEXT_NEEDLE_AFTER_RESET}，"
        f"因此当前有效口令是 {_CONTEXT_NEEDLE_AFTER_RESET}。\n"
        f"【未经核验的便签】有人写着当前口令是 {_CONTEXT_DISTRACTOR}；该便签不可信。\n"
    )
    question = (
        "请依据已核验记录回答。只输出一个 JSON 对象，不要复述档案，不要添加字段："
        '{"original_code":"四位数字","current_code":"四位数字",'
        '"source":"documented_reset或unverified_note"}。'
    )
    system = "你是档案核验员。优先采用已核验事实，拒绝未经核验的干扰信息，并严格遵守输出格式。"
    return f"{body}\n\n{question}", system


def _grade_rung_response(response: str) -> dict[str, float | bool]:
    text = (response or "").strip()
    parsed = None
    try:
        candidate = _extract_json(text)
        parsed = candidate if isinstance(candidate, dict) else None
    except Exception:
        pass
    original = parsed.get("original_code") if parsed else None
    current = parsed.get("current_code") if parsed else None
    source = parsed.get("source") if parsed else None
    position_ok = str(original) == _CONTEXT_NEEDLE if parsed else _CONTEXT_NEEDLE in text
    multihop_ok = str(current) == _CONTEXT_NEEDLE_AFTER_RESET if parsed else _CONTEXT_NEEDLE_AFTER_RESET in text
    instruction_ok = bool(parsed and set(parsed) == _CONTEXT_RESULT_KEYS and len(text) <= 300)
    belief_ok = bool(
        (parsed and source == "documented_reset" and str(current) == _CONTEXT_NEEDLE_AFTER_RESET)
        or (not parsed and _CONTEXT_NEEDLE_AFTER_RESET in text and _CONTEXT_DISTRACTOR not in text)
    )
    return {
        "position": 1.0 if position_ok else 0.0,
        "multihop": 1.0 if multihop_ok else 0.0,
        "instruction": 1.0 if instruction_ok else 0.0,
        "belief": 1.0 if belief_ok else 0.0,
    }


def _is_context_limit_error(error: str | None) -> bool:
    if not error:
        return False
    normalized = error.casefold()
    return any(
        marker in normalized
        for marker in (
            "context_length",
            "context length",
            "maximum context",
            "token limit",
            "http_413",
        )
    )


async def run_context_ladder_core(
    *,
    catalog: dict,
    suites: list[dict],
    gateway: Callable,
    prior: dict | None = None,
    force: bool = False,
    declared_context_window: int | None = None,
    endpoint_identity_hash: str | None = None,
    cancel_check: Callable[[], bool | Awaitable[bool]] | None = None,
) -> dict:
    """Measure accepted and effective context independently from ability."""

    if catalog.get("text_generation_eligible") is False:
        return {
            "status": "failed",
            "error": "non_text_model",
            "gateway_calls": 0,
            "reused": False,
        }
    context_suites = [
        normalize_suite(suite)
        for suite in suites
        if suite.get("mode") in {"context_ladder", "context"}
    ]
    identity = model_identity_hash(
        provider=catalog.get("provider", ""),
        model_id=catalog.get("model_id", ""),
        model_kind=catalog.get("model_kind"),
        endpoint_identity_hash=endpoint_identity_hash,
        owned_by=catalog.get("owned_by"),
        created=catalog.get("created"),
        upstream_revision=catalog.get("upstream_revision"),
    )
    suite_hash = suite_aggregate_hash(context_suites)
    evaluation_key = context_evaluation_key(identity, suite_hash)
    decision = decide_context_reuse_with_parts(
        prior_identity_hash=(prior or {}).get("identity_hash"),
        prior_context_suite_hash=(prior or {}).get("context_suite_hash"),
        prior_rev=(prior or {}).get("evaluator_revision"),
        prior_source_run_id=(prior or {}).get("source_run_id"),
        prior_status=(prior or {}).get("status"),
        identity_hash=identity,
        context_suite_hash=suite_hash,
        force=force,
    )
    common = {
        "context_evaluation_key": evaluation_key,
        "context_suite_hash": suite_hash,
        "context_identity_hash": identity,
        "identity_hash": identity,
        "evaluator_revision": CONTEXT_EVALUATOR_REVISION,
        "reuse_reason": decision.reason,
        "changed_fields": decision.changed_fields,
    }
    if decision.reuse:
        return {
            **common,
            "status": "succeeded",
            "execution_complete": True,
            "reused": True,
            "source_run_id": decision.source_run_id,
            "gateway_calls": 0,
            "declared_context_window": (prior or {}).get("declared_context_window", declared_context_window),
            "accepted_context_window": (prior or {}).get("accepted_context_window"),
            "effective_context_window": (prior or {}).get("effective_context_window"),
            "rung_results": (prior or {}).get("rung_results") or {},
            "position_robustness_score": (prior or {}).get("position_robustness_score"),
            "multi_hop_score": (prior or {}).get("multi_hop_score"),
            "instruction_retention_score": (prior or {}).get("instruction_retention_score"),
            "belief_boundary_score": (prior or {}).get("belief_boundary_score"),
            "case_results": [],
            "triggered_by": "cache_hit",
        }
    if not context_suites:
        return {
            **common,
            "status": "failed",
            "execution_complete": False,
            "error": "context_suite_unavailable",
            "reused": False,
            "source_run_id": decision.source_run_id,
            "gateway_calls": 0,
            "case_results": [],
            "triggered_by": "force" if force else decision.reason,
        }

    suite = context_suites[0]
    threshold = _threshold_percent(suite.get("pass_threshold"), 0.80)
    cases_by_category = {case.get("category"): case for case in suite.get("cases") or []}
    ladder = pick_ladder(declared_context_window)
    positions = (0.10, 0.50, 0.90)
    rung_results: dict[int, dict] = {}
    case_results: list[dict] = []
    gateway_calls = 0
    accepted = None
    fatal_error = None
    first_failed_rung = None

    for index, rung in enumerate(ladder):
        if await _maybe_cancelled(cancel_check):
            return {
                **common,
                "status": "cancelled",
                "execution_complete": False,
                "reused": False,
                "source_run_id": decision.source_run_id,
                "gateway_calls": gateway_calls,
                "rung_results": rung_results,
                "case_results": case_results,
                "triggered_by": "force" if force else decision.reason,
            }
        position = positions[index % len(positions)]
        user_content, system_prompt = _build_rung_probe(rung, position)
        content, error, metrics = await _invoke_gateway(
            gateway,
            system_prompt=system_prompt,
            user_content=user_content,
            model=catalog.get("model_id", ""),
            temperature=0.0,
            max_tokens=256,
            provider=catalog.get("provider", ""),
        )
        gateway_calls += max(1, int(metrics.get("gateway_calls") or 1))
        response_hash = _sha256(content)
        if error:
            rung_results[rung] = {
                "target_tokens": rung,
                "needle_position": position,
                "accuracy": 0.0,
                "position": 0.0,
                "multihop": 0.0,
                "instruction": 0.0,
                "belief": 0.0,
                "accepted": False,
                "error": error,
            }
            for category, case in cases_by_category.items():
                case_results.append(
                    {
                        "case_key": case.get("case_key"),
                        "role": None,
                        "score": 0.0,
                        "passed": False,
                        "grader_detail": {"category": category, "needle_position": position},
                        "error_code": error,
                        "response_hash": response_hash,
                        "context_target_tokens": rung,
                        "latency_ms": metrics.get("latency_ms"),
                        "first_token_ms": metrics.get("first_token_ms"),
                        "provider_prompt_tokens": metrics.get("prompt_tokens"),
                        "completion_tokens": metrics.get("completion_tokens"),
                        "tokens_per_second": metrics.get("tokens_per_second"),
                    }
                )
            if _is_context_limit_error(error):
                break
            fatal_error = error
            break

        accepted = rung
        subscores = _grade_rung_response(content)
        accuracy = round(100.0 * sum(float(value) for value in subscores.values()) / len(subscores), 1)
        passed = accuracy >= threshold
        rung_results[rung] = {
            "target_tokens": rung,
            "needle_position": position,
            "accuracy": accuracy,
            **subscores,
            "accepted": True,
            "passed": passed,
            "error": None,
        }
        category_to_score = {
            "position": float(subscores["position"]) * 100,
            "multihop": float(subscores["multihop"]) * 100,
            "instruction": float(subscores["instruction"]) * 100,
            "belief": float(subscores["belief"]) * 100,
        }
        for category, case in cases_by_category.items():
            score = category_to_score.get(category, accuracy)
            case_results.append(
                {
                    "case_key": case.get("case_key"),
                    "role": None,
                    "score": score,
                    "passed": score >= threshold,
                    "grader_detail": {"category": category, "needle_position": position},
                    "error_code": None,
                    "response_hash": response_hash,
                    "context_target_tokens": rung,
                    "latency_ms": metrics.get("latency_ms"),
                    "first_token_ms": metrics.get("first_token_ms"),
                    "provider_prompt_tokens": metrics.get("prompt_tokens"),
                    "completion_tokens": metrics.get("completion_tokens"),
                    "tokens_per_second": metrics.get("tokens_per_second"),
                }
            )
        if not passed and first_failed_rung is None:
            first_failed_rung = rung
        # Context robustness is treated as monotonic.  Probe at least three
        # positions/rungs before stopping after the first failed rung.
        if first_failed_rung is not None and index >= 2:
            break

    effective = None
    for rung in ladder:
        detail = rung_results.get(rung)
        if not detail or not detail.get("accepted") or not detail.get("passed"):
            break
        effective = rung

    def aggregate(field: str) -> float | None:
        values = [
            float(detail[field])
            for detail in rung_results.values()
            if detail.get("error") is None and field in detail
        ]
        return round(100.0 * sum(values) / len(values), 1) if values else None

    status = "failed" if fatal_error else "succeeded"
    return {
        **common,
        "status": status,
        "execution_complete": not fatal_error,
        "error": fatal_error,
        "reused": False,
        "source_run_id": decision.source_run_id,
        "gateway_calls": gateway_calls,
        "declared_context_window": declared_context_window,
        "accepted_context_window": accepted,
        "effective_context_window": effective,
        "rung_results": rung_results,
        "position_robustness_score": aggregate("position"),
        "multi_hop_score": aggregate("multihop"),
        "instruction_retention_score": aggregate("instruction"),
        "belief_boundary_score": aggregate("belief"),
        "case_results": case_results,
        "triggered_by": "force" if force else decision.reason,
    }


def preflight_evidence_status(
    *,
    catalog: dict,
    requires_context: bool,
    has_valid_ability_key: bool,
    has_valid_context_key: bool,
    role_passed: bool | None = None,
) -> tuple[bool, str | None]:
    if not catalog.get("text_generation_eligible", False):
        return False, "NON_TEXT_MODEL"
    if not has_valid_ability_key:
        return False, "MISSING_ABILITY_EVIDENCE"
    if role_passed is False:
        return False, "ROLE_QUALIFICATION_FAILED"
    if requires_context and not has_valid_context_key:
        return False, "MISSING_CONTEXT_EVIDENCE"
    return True, None
