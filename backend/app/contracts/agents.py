"""Pydantic v2 agent contracts — single source for JSON Schema (AI__.md v3.0 §9).

Rules:
- model_config = ConfigDict(extra=\"forbid\")
- Prompt output_schema MUST come from model_json_schema()
- call_agent validates structured roles with these models (fail-closed)
"""
from __future__ import annotations

from typing import Any, Literal, Type

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    # extra=forbid: reject unknown keys; strict=True: no loose type coercion
    model_config = ConfigDict(extra="forbid", strict=True)


# ── shared ──────────────────────────────────────────────────────────


class EvidenceRef(StrictModel):
    paragraph_key: str | None = None
    content_hash: str | None = None
    quote: str = Field(default="", max_length=500)


# ── review ──────────────────────────────────────────────────────────


class ReviewIssueContract(StrictModel):
    issue_id: str = Field(min_length=1, max_length=120)
    issue_cluster_id: str | None = None
    severity: Literal["blocker", "major", "minor", "critical", "warning", "info"] = "major"
    category: str = Field(default="continuity", max_length=80)
    message: str | None = None
    reason: str | None = None
    evidence: list[EvidenceRef] | list[str] | str | None = None
    target_paragraph_key: str | None = None
    paragraph_id: str | None = None
    scene_id: str | None = None
    repair_instruction: str | None = None
    acceptance: str | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def _norm_sev(cls, v: Any) -> str:
        if v is None:
            return "major"
        s = str(v).lower().strip()
        mapping = {
            "crit": "critical",
            "error": "major",
            "high": "major",
            "med": "minor",
            "medium": "minor",
            "low": "minor",
            "warn": "warning",
        }
        s = mapping.get(s, s)
        if s not in ("blocker", "major", "minor", "critical", "warning", "info"):
            return "major"
        return s


class ReviewReportContract(StrictModel):
    """Production review payload. Supports legacy `passed` and v2 `verdict`."""

    schema_version: Literal["review-v1", "review-v2"] | None = None
    passed: bool | None = None
    verdict: Literal["pass", "revise", "needs_human"] | None = None
    issues: list[ReviewIssueContract] = Field(default_factory=list)
    beat_coverage: list[dict[str, Any]] | None = None

    def as_pipeline_tuple(self) -> tuple[bool, list[dict[str, Any]]]:
        if self.passed is not None:
            ok = bool(self.passed)
        elif self.verdict is not None:
            ok = self.verdict == "pass"
        else:
            # no blockers/majors/criticals => pass
            bad = {"blocker", "major", "critical"}
            ok = not any((i.severity in bad) for i in self.issues)
        return ok, [i.model_dump(mode="json") for i in self.issues]


# ── planner ─────────────────────────────────────────────────────────


class ScenePlanItem(StrictModel):
    scene_no: int | None = None
    goal: str | None = None
    conflict: str | None = None
    beats: list[Any] | None = None
    required_beats: list[Any] | None = None
    characters: list[Any] | None = None
    location: str | None = None
    exit_state: str | None = None
    target_word_count: int | None = None
    summary: str | None = None
    pov_character_id: str | None = None
    # allow common planner free-form keys via model_extra? forbid — keep optional dict bag
    notes: str | None = None


class ChapterPlanContract(StrictModel):
    chapter_goal: str = Field(min_length=1)
    scenes: list[dict[str, Any]] = Field(min_length=1)
    required_beat_mapping: list[Any] = Field(default_factory=list)
    source: Literal["model", "deterministic_fallback"] | None = None
    blocked: bool | None = None
    block_reason: str | None = None


# ── patch ───────────────────────────────────────────────────────────


class PatchContract(StrictModel):
    replacement_text: str = Field(min_length=1)
    resolved_issue_ids: list[str] = Field(default_factory=list)
    target_paragraph_key: str | None = None
    expected_hash: str | None = None


# ── state extractor ─────────────────────────────────────────────────


class ExtractEventContract(StrictModel):
    event_key: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    field: str | None = None
    old_value: Any = None
    new_value: Any = None
    certainty: Literal["explicit", "high", "inferred", "low", "speculation"] | str = "explicit"
    scene_no: int | None = None
    evidence_paragraph_key: str | None = None
    evidence_paragraph_keys: list[str] | None = None
    evidence_hash: str | None = None
    evidence: str | None = None
    evidence_excerpt: str | None = None
    event_type: str | None = None
    subject_entity_ids: list[Any] | None = None
    object_entity_ids: list[Any] | None = None


class StateExtractContract(StrictModel):
    events: list[ExtractEventContract] = Field(default_factory=list)
    conflicts: list[Any] = Field(default_factory=list)
    l1_chapter_ledger: dict[str, Any] | None = None


# ── query / rank / outline / drift ──────────────────────────────────


class QueryPlanContract(StrictModel):
    character_ids: list[Any] = Field(default_factory=list)
    event_types: list[Any] = Field(default_factory=list)
    chapter_range: dict[str, Any] | None = None
    semantic_questions: list[Any] = Field(default_factory=list)
    location_ids: list[Any] | None = None
    item_ids: list[Any] | None = None
    plot_thread_ids: list[Any] | None = None
    required_outline_node_ids: list[Any] | None = None


class EvidenceRankContract(StrictModel):
    ranked_candidates: list[Any] = Field(default_factory=list)
    missing_evidence: list[Any] = Field(default_factory=list)
    conflicts: list[Any] = Field(default_factory=list)


class OutlineParseContract(StrictModel):
    outline_version: int | None = None
    nodes: list[Any] = Field(default_factory=list)
    unresolved_dependencies: list[Any] = Field(default_factory=list)
    validation_errors: list[Any] = Field(default_factory=list)


class DriftAuditContract(StrictModel):
    status: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    redline_findings: list[Any] = Field(default_factory=list)


# ── registry ────────────────────────────────────────────────────────

ROLE_CONTRACTS: dict[str, Type[BaseModel]] = {
    "review_agent": ReviewReportContract,
    "chapter_planner": ChapterPlanContract,
    "local_rewrite_editor": PatchContract,
    "state_extractor": StateExtractContract,
    "query_planner": QueryPlanContract,
    "evidence_ranker": EvidenceRankContract,
    "outline_parser": OutlineParseContract,
    "drift_audit": DriftAuditContract,
    # research_synth / aileak_judge: soft — no strict contract yet
}


def get_contract(agent_role: str) -> Type[BaseModel] | None:
    return ROLE_CONTRACTS.get(agent_role)


def schema_for_role(agent_role: str) -> dict[str, Any] | None:
    model = get_contract(agent_role)
    if not model:
        return None
    return openai_compatible_schema(model)


def openai_compatible_schema(model: Type[BaseModel]) -> dict[str, Any]:
    """Build JSON Schema suitable for response_format json_schema.

    OpenAI-style strict mode wants object + additionalProperties false + required.
    Optional fields (T | None) become anyOf — we simplify to the non-null branch
    and keep the field out of required when it had a default / was optional.
    """
    raw = model.model_json_schema()
    return _harden_schema(raw)


def _unwrap_null_union(node: dict[str, Any]) -> dict[str, Any]:
    """Collapse anyOf:[{...},{type:null}] into the non-null schema."""
    if "anyOf" in node and isinstance(node["anyOf"], list):
        non_null = [x for x in node["anyOf"] if not (isinstance(x, dict) and x.get("type") == "null")]
        if len(non_null) == 1 and isinstance(non_null[0], dict):
            merged = dict(non_null[0])
            for k, v in node.items():
                if k != "anyOf" and k not in merged:
                    merged[k] = v
            return merged
    return node


def _harden_schema(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    node = _unwrap_null_union(node)
    out = dict(node)
    # resolve/remove unsupported keys that break some gateways
    for k in ("title", "description", "examples", "default"):
        # keep description optional; drop title noise
        if k == "title" and k in out:
            out.pop(k, None)
    if out.get("type") == "object" or "properties" in out:
        out.setdefault("type", "object")
        props = out.get("properties") or {}
        out["properties"] = {k: _harden_schema(v) for k, v in props.items()}
        # additionalProperties false for extra=forbid contracts
        out["additionalProperties"] = False
        # required: prefer existing, else all keys (strict-ish)
        if "required" not in out and props:
            # only require keys without defaults if $defs allow — use all for gateway
            out["required"] = list(props.keys())
    if "items" in out:
        out["items"] = _harden_schema(out["items"])
    if "$defs" in out:
        out["$defs"] = {k: _harden_schema(v) for k, v in out["$defs"].items()}
    if "anyOf" in out:
        out["anyOf"] = [_harden_schema(x) for x in out["anyOf"]]
    if "oneOf" in out:
        out["oneOf"] = [_harden_schema(x) for x in out["oneOf"]]
    if "allOf" in out:
        out["allOf"] = [_harden_schema(x) for x in out["allOf"]]
    return out


def validate_payload(agent_role: str, payload: Any) -> tuple[Any | None, str | None]:
    """Return (validated_dict_or_none, error). Fail closed."""
    model = get_contract(agent_role)
    if model is None:
        return payload if isinstance(payload, (dict, list, str)) else payload, None
    if payload is None:
        return None, "empty_payload"
    try:
        if isinstance(payload, str):
            obj = model.model_validate_json(payload)
        elif isinstance(payload, dict):
            obj = model.model_validate(payload)
        else:
            return None, f"unsupported_payload_type:{type(payload).__name__}"
        return obj.model_dump(mode="json"), None
    except Exception as e:
        return None, f"pydantic_validation_failed:{e}"


def response_format_for_role(agent_role: str, *, strict: bool = True) -> dict[str, Any] | None:
    schema = schema_for_role(agent_role)
    if not schema:
        return None
    name = f"{agent_role}_v1".replace("-", "_")[:64]
    import json as _json
    blob = _json.dumps(schema)
    # anyOf/$ref remaining → gateway strict often 400; keep pydantic as the real gate
    use_strict = bool(strict) and ("anyOf" not in blob) and ('"$ref"' not in blob)
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": use_strict,
            "schema": schema,
        },
    }
