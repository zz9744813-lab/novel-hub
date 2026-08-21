"""Mechanical consistency gate (AI__.md v3.0 B-08 + v9 CCNE §27).

Deterministic checks only — no LLM. Replaces empty consistency_check hop.

v9: accepts structured causal inputs (scene_contract / pre_state /
post_state_candidates / core_anchors) and surfaces CCNE error codes
(CAUSAL_PRECONDITION_FAILED, HARD_EFFECT_MISSING, ILLEGAL_KNOWLEDGE, ...).
The gate never guesses prose semantics — structured data in, findings out.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.contracts.narrative import SceneContract, StateDelta
from app.engine.causal_engine import CausalEngine

GATE_VERSION = "v9"

# Red-line codes (spec §28.2): can never be offset by a passing total.
RED_LINE_CODES = {
    "ILLEGAL_KNOWLEDGE",
    "HARD_EFFECT_CONTRADICTED",
    "HARD_EFFECT_MISSING",
    "CAUSAL_PRECONDITION_FAILED",
    "UNSUPPORTED_HARD_STATE_CHANGE",
}


@dataclass
class ConsistencyResult:
    ok: bool
    findings: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "findings": self.findings, "gate_version": GATE_VERSION}


_META_LEAK = re.compile(
    r"(PIPELINE_BLOCKED|作为AI|作为人工智能|以下是正文|字数统计|JSON Schema|```json)",
    re.I,
)


def _coerce_contract(scene_contract: Any) -> SceneContract | None:
    if scene_contract is None:
        return None
    if isinstance(scene_contract, SceneContract):
        return scene_contract
    if isinstance(scene_contract, dict):
        try:
            return SceneContract.model_validate(scene_contract)
        except Exception:
            return None
    return None


def _coerce_deltas(raw: Any) -> list[StateDelta]:
    if not raw:
        return []
    out: list[StateDelta] = []
    for item in raw:
        if isinstance(item, StateDelta):
            out.append(item)
        elif isinstance(item, dict):
            try:
                out.append(StateDelta.model_validate(item))
            except Exception:
                continue
    return out


def run_mechanical_consistency(
    *,
    chapter_content: str,
    scenes: list[dict] | None = None,
    outline_data: dict | None = None,
    scene_plan: dict | None = None,
    scene_contract: Any = None,
    pre_state: dict[str, Any] | None = None,
    post_state_candidates: list[Any] | None = None,
    core_anchors: list[dict] | None = None,
    min_chars: int = 200,
) -> ConsistencyResult:
    findings: list[dict] = []
    content = chapter_content or ""
    outline_data = outline_data or {}
    scenes = scenes or []

    if len(content.strip()) < min_chars:
        findings.append(
            {
                "code": "content_too_short",
                "severity": "blocker",
                "message": f"chapter content length {len(content.strip())} < {min_chars}",
            }
        )

    if _META_LEAK.search(content):
        findings.append(
            {
                "code": "meta_leak",
                "severity": "blocker",
                "message": "meta/instruction leak markers found in content",
            }
        )

    # Multi-scene: each scene must be non-empty
    for sc in scenes:
        c = (sc.get("content") or "").strip()
        if not c:
            findings.append(
                {
                    "code": "empty_scene",
                    "severity": "blocker",
                    "message": f"scene {sc.get('scene_no')} empty",
                    "scene_no": sc.get("scene_no"),
                }
            )

    # Forbidden outcomes: simple substring presence (explicit text only)
    forbidden = outline_data.get("forbidden_outcomes") or []
    if isinstance(forbidden, list):
        for fo in forbidden:
            if not fo:
                continue
            text = fo if isinstance(fo, str) else str(fo.get("text") or fo.get("outcome") or "")
            text = text.strip()
            if len(text) >= 4 and text in content:
                findings.append(
                    {
                        "code": "forbidden_outcome_present",
                        "severity": "major",
                        "message": f"forbidden outcome text present: {text[:80]}",
                    }
                )

    # Required beats: if plan/outline lists string beats, require soft presence
    # (keyword token in content) — only flag when beat is a short explicit string
    beats: list[str] = []
    rb = outline_data.get("required_beats") or []
    if isinstance(rb, list):
        for b in rb:
            if isinstance(b, str) and 2 <= len(b) <= 40:
                beats.append(b)
            elif isinstance(b, dict):
                t = b.get("beat") or b.get("text") or b.get("id")
                if isinstance(t, str) and 2 <= len(t) <= 40:
                    beats.append(t)
    if scene_plan and isinstance(scene_plan.get("required_beat_mapping"), list):
        for m in scene_plan["required_beat_mapping"]:
            if isinstance(m, dict):
                t = m.get("beat") or m.get("beat_id")
                if isinstance(t, str) and 2 <= len(t) <= 40:
                    beats.append(t)

    # Soft: only when beats look like concrete Chinese/English phrases present in outline
    # Do not fail closed on missing poetic beat ids — only exact short phrases
    for beat in beats[:20]:
        # skip ids like beat_01
        if re.fullmatch(r"[A-Za-z0-9_\-]+", beat) and "_" in beat:
            continue
        if beat not in content:
            findings.append(
                {
                    "code": "required_beat_not_found",
                    "severity": "minor",
                    "message": f"required beat phrase not found: {beat}",
                }
            )

    # ── v9 CCNE: structured causal validation (spec §27) ────────────
    contract = _coerce_contract(scene_contract)
    if contract is not None:
        engine = CausalEngine()
        anchor_ids: set[str] = set()
        for a in core_anchors or []:
            if isinstance(a, dict):
                code = a.get("anchor_code") or a.get("id")
                if code:
                    anchor_ids.add(str(code))

        next_state, report = engine.simulate_scene(
            contract, pre_state or {}, anchor_ids or None
        )

        for f in report.findings:
            findings.append(
                {
                    "code": f.code,
                    "severity": f.severity,
                    "message": f.detail,
                    "scene_no": f.scene_no or contract.scene_no,
                    "character_id": f.character_id,
                    "path": f.path,
                    "source": "causal_engine",
                }
            )

        # Observed post-state candidates vs expected hard effects (CC-05)
        if post_state_candidates:
            observed: list[StateDelta] = []
            for cand in post_state_candidates:
                observed.extend(_coerce_deltas(cand))
            if observed:
                hard_report = engine.check_hard_effect_contradictions(
                    contract.expected_effects, observed, scene_no=contract.scene_no
                )
                for f in hard_report.findings:
                    findings.append(
                        {
                            "code": f.code,
                            "severity": f.severity,
                            "message": f.detail,
                            "scene_no": f.scene_no or contract.scene_no,
                            "path": f.path,
                            "source": "causal_engine",
                        }
                    )

        # must_realize: contract's hard requirements need explicit realization
        for req in contract.must_realize or []:
            if isinstance(req, str) and len(req) >= 4 and req not in content:
                findings.append(
                    {
                        "code": "contract_must_realize_missing",
                        "severity": "major",
                        "message": f"scene contract must_realize item not realized: {req[:80]}",
                        "scene_no": contract.scene_no,
                        "source": "causal_engine",
                    }
                )

        for forbidden_assert in contract.must_not_assert or []:
            if (
                isinstance(forbidden_assert, str)
                and len(forbidden_assert) >= 4
                and forbidden_assert in content
            ):
                findings.append(
                    {
                        "code": "contract_must_not_assert_present",
                        "severity": "blocker",
                        "message": f"contract must_not_assert item present: {forbidden_assert[:80]}",
                        "scene_no": contract.scene_no,
                        "source": "causal_engine",
                    }
                )

    # Blockers only fail the gate; major may also fail for forbidden
    hard = [f for f in findings if f.get("severity") in ("blocker", "major")]
    return ConsistencyResult(ok=len(hard) == 0, findings=findings)
