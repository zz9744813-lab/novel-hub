"""Deterministic chapter-length contracts used by planning and release gates."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable


DEFAULT_CHAPTER_TARGET = (2400, 3600)


@dataclass(frozen=True)
class ChapterLengthTarget:
    minimum_chars: int
    maximum_chars: int
    target_chars: int


def parse_chapter_target_chars(
    raw: Any,
    *,
    default: tuple[int, int] = DEFAULT_CHAPTER_TARGET,
) -> ChapterLengthTarget:
    """Parse a JSON/list range without silently accepting malformed settings."""
    if raw is None or raw == "":
        values: Any = list(default)
    elif isinstance(raw, str):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("chapter_target_chars must be valid JSON") from exc
    else:
        values = raw
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        raise ValueError("chapter_target_chars must contain [minimum, maximum]")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise ValueError("chapter_target_chars values must be numeric")
    lower, upper = (int(values[0]), int(values[1]))
    if lower < 1000 or upper > 30_000 or lower > upper:
        raise ValueError("chapter_target_chars must be ordered within 1000..30000")
    return ChapterLengthTarget(lower, upper, (lower + upper) // 2)


def distribute_scene_targets(
    target_chars: int,
    requested_weights: Iterable[int | float | None],
    *,
    minimum_scene_chars: int = 500,
) -> list[int]:
    """Allocate an exact chapter target while retaining a planner's relative weights."""
    weights = []
    for value in requested_weights:
        try:
            weight = float(value or 0)
        except (TypeError, ValueError):
            weight = 0
        weights.append(weight if weight > 0 else 1.0)
    if not weights:
        return []
    floor = min(minimum_scene_chars, max(1, target_chars // len(weights)))
    remaining = max(0, target_chars - floor * len(weights))
    weight_total = sum(weights)
    raw_shares = [remaining * weight / weight_total for weight in weights]
    shares = [int(value) for value in raw_shares]
    leftover = remaining - sum(shares)
    order = sorted(
        range(len(weights)),
        key=lambda index: raw_shares[index] - shares[index],
        reverse=True,
    )
    for index in order[:leftover]:
        shares[index] += 1
    result = [floor + share for share in shares]
    assert sum(result) == target_chars
    return result


def chapter_length_issues(
    content: str,
    target: ChapterLengthTarget,
) -> list[dict[str, Any]]:
    """Return patch-compatible quality issues; never soft-pass a large miss."""
    actual = len((content or "").strip())
    if actual < target.minimum_chars:
        return [
            {
                "issue_id": "chapter_length_below_contract",
                "issue_cluster_id": "chapter_length",
                "severity": "major",
                "category": "length_contract",
                "message": (
                    f"正文 {actual} 字，低于合同下限 {target.minimum_chars} 字；"
                    "补足必须通过行动、阻力、转折或余波，不得灌水。"
                ),
            }
        ]
    if actual > target.maximum_chars:
        return [
            {
                "issue_id": "chapter_length_above_contract",
                "issue_cluster_id": "chapter_length",
                "severity": "major",
                "category": "length_contract",
                "message": (
                    f"正文 {actual} 字，超过合同上限 {target.maximum_chars} 字；"
                    "压缩重复说明和无状态变化段落，不得删掉因果必需场景。"
                ),
            }
        ]
    return []


__all__ = [
    "ChapterLengthTarget",
    "DEFAULT_CHAPTER_TARGET",
    "chapter_length_issues",
    "distribute_scene_targets",
    "parse_chapter_target_chars",
]
