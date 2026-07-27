"""Mechanical consistency gate (AI__.md v3.0 B-08).

Deterministic checks only — no LLM. Replaces empty consistency_check hop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConsistencyResult:
    ok: bool
    findings: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "findings": self.findings}


_META_LEAK = re.compile(
    r"(PIPELINE_BLOCKED|作为AI|作为人工智能|以下是正文|字数统计|JSON Schema|```json)",
    re.I,
)


def run_mechanical_consistency(
    *,
    chapter_content: str,
    scenes: list[dict] | None = None,
    outline_data: dict | None = None,
    scene_plan: dict | None = None,
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

    # Blockers only fail the gate; major may also fail for forbidden
    hard = [f for f in findings if f.get("severity") in ("blocker", "major")]
    return ConsistencyResult(ok=len(hard) == 0, findings=findings)
