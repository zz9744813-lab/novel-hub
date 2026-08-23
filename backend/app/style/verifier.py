"""Style Verifier + Drift (spec §48-§53).

- verify_draft_style: deterministic style verification after draft (metrics vs
  the profile's StyleMetricRange) — a finding is a structured deviation, not a
  bare string.
- build_style_patch_issue: convert a style finding into a Local Patch issue that
  reuses the existing local_rewrite_editor (spec §51 — patch rhythm/dialogue/
  distance/emotion, protect events/facts/causality).
- compute_style_drift: rolling style distance across chapters (spec §53).
"""
from __future__ import annotations

from typing import Any

from app.style.metrics import compute_fingerprint, extract_style_metrics, fingerprint_distance

def verify_draft_style(content: str, metric_ranges: dict) -> dict[str, Any]:
    """Deterministic style verification against the profile's metric ranges."""
    metrics = extract_style_metrics(content)
    findings: list[dict[str, Any]] = []

    checks = [
        ("dialogue.dialogue_ratio", metrics["dialogue"]["dialogue_ratio"], "STYLE_DIALOGUE_RATIO"),
        ("surface.sentence_chars_mean", metrics["surface"]["sentence_chars_mean"], "STYLE_SENTENCE_LENGTH"),
        ("surface.lexical_diversity", metrics["surface"]["lexical_diversity"], "STYLE_LEXICAL_DIVERSITY"),
    ]

    for key, actual, code in checks:
        r = metric_ranges.get(key)
        if not r:
            continue
        hard_min = float(r.get("hard_min", 0))
        hard_max = float(r.get("hard_max", 1))
        preferred = [float(r.get("preferred_min", hard_min)), float(r.get("preferred_max", hard_max))]
        if actual < hard_min:
            findings.append({
                "code": f"{code}_LOW",
                "target": preferred,
                "actual": round(actual, 4),
                "severity": "major",
            })
        elif actual > hard_max:
            findings.append({
                "code": f"{code}_HIGH",
                "target": preferred,
                "actual": round(actual, 4),
                "severity": "major",
            })

    return {"passed": not findings, "findings": findings, "metrics": metrics}


def _finding_instruction(finding: dict[str, Any]) -> str:
    code = str(finding.get("code", ""))
    actual = finding.get("actual", 0)
    target = finding.get("target", [0, 1])
    if "DIALOGUE_RATIO" in code:
        return (
            f"当前对话占比 {actual:.2f}，目标范围 {target[0]:.2f}~{target[1]:.2f}。"
            "调整对白密度，但不得改动事件、事实、因果、人物知识与状态。"
        )
    if "SENTENCE_LENGTH" in code:
        return (
            f"当前句长均值 {actual:.1f}，目标范围 {target[0]:.0f}~{target[1]:.0f}。"
            "调整句长节奏，但不得改动事实和因果。"
        )
    if "LEXICAL" in code:
        return (
            f"当前词汇多样度 {actual:.2f}，目标范围 {target[0]:.2f}~{target[1]:.2f}。"
            "调整用词多样性，但不得改动事实和因果。"
        )
    return "调整文风，但不得改动事件、事实和因果。"


def build_style_patch_issue(finding: dict[str, Any]) -> dict[str, Any]:
    """Convert a style finding into a Local Patch issue (spec §51)."""
    return {
        "issue_id": f"style-{str(finding.get('code', 'finding')).lower()}",
        "category": "style",
        "severity": finding.get("severity", "major"),
        "instruction": _finding_instruction(finding),
        "protected_facts": finding.get("protected_facts", []),
        "paragraph_id": finding.get("paragraph_id"),
    }


def compute_style_drift(
    profile_fingerprint: list[float],
    chapter_metrics_list: list[dict],
    *,
    drift_threshold: float = 1.0,
) -> dict[str, Any]:
    """Rolling style distance across chapters (spec §53)."""
    distances: list[float] = []
    for m in chapter_metrics_list:
        fp = compute_fingerprint(m)
        distances.append(fingerprint_distance(fp, profile_fingerprint))

    if not distances:
        return {"style_distance_mean": 0.0, "style_distance_max": 0.0, "latest_distance": 0.0, "drift_triggered": False, "per_chapter": []}

    return {
        "style_distance_mean": round(sum(distances) / len(distances), 4),
        "style_distance_max": round(max(distances), 4),
        "latest_distance": round(distances[-1], 4),
        "drift_triggered": distances[-1] > drift_threshold,
        "per_chapter": [round(d, 4) for d in distances],
    }


async def semantic_judge_style(
    book_id: Any,
    *,
    content: str,
    semantic_targets: dict | None = None,
) -> dict[str, Any]:
    """Optional semantic judge for hard-to-quantify dimensions (spec §50).

    Only invoked when deterministic findings are borderline. Reuses the
    style_analyzer contract to judge a draft's narrative distance / subtext /
    emotion explicitness — it is NOT called on every draft.
    """
    try:
        from app.agents.style_analyzer import run_style_analyzer

        result = await run_style_analyzer(
            book_id,
            segments=[content[:2000]],
            deterministic_metrics={},
            genre_hint=None,
        )
    except Exception as e:  # pragma: no cover
        return {"judged": False, "error": str(e)}

    if "error" in result:
        return {"judged": False, "error": result["error"]}

    return {
        "judged": True,
        "narrative": result.get("narrative", {}),
        "dialogue": result.get("dialogue", {}),
        "emotion_expression": result.get("emotion_expression", {}),
        "targets": semantic_targets or {},
    }
