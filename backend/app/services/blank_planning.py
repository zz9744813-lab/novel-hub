"""Validation and persistence-neutral helpers for blank-book planning."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class PlanningValidationError(ValueError):
    """The model output cannot safely become a planning draft."""


def _text(value: Any, field: str, *, required: bool = False) -> str:
    if value is None:
        if required:
            raise PlanningValidationError(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise PlanningValidationError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise PlanningValidationError(f"{field} is required")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PlanningValidationError(f"{field} must be a list of strings")
    return [item.strip() for item in value if item.strip()]


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PlanningValidationError(f"{field} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PlanningValidationError(f"{field} must be a positive integer") from exc
    if result < 1:
        raise PlanningValidationError(f"{field} must be a positive integer")
    return result


def normalize_planning_draft(payload: Mapping[str, Any], *, target_chapter_count: int) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise PlanningValidationError("planning output must be an object")

    requested = _positive_int(target_chapter_count, "target_chapter_count")
    title = _text(payload.get("title"), "title", required=True)
    logline = _text(payload.get("logline"), "logline", required=True)
    synopsis = _text(payload.get("synopsis"), "synopsis", required=True)
    genre = _text(payload.get("genre"), "genre")
    tone = _text(payload.get("tone"), "tone")
    themes = _string_list(payload.get("themes"), "themes")

    raw_chapters = payload.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise PlanningValidationError("chapters must be a non-empty list")
    if len(raw_chapters) != requested:
        raise PlanningValidationError(
            f"chapters must contain exactly {requested} items; received {len(raw_chapters)}"
        )

    chapters: list[dict[str, Any]] = []
    numbers: set[int] = set()
    for raw in raw_chapters:
        if not isinstance(raw, Mapping):
            raise PlanningValidationError("each chapter must be an object")
        chapter_no = _positive_int(raw.get("chapter_no"), "chapter_no")
        if chapter_no in numbers:
            raise PlanningValidationError("chapter_no values must be unique")
        numbers.add(chapter_no)
        depends_on = raw.get("depends_on", [])
        if not isinstance(depends_on, list) or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in depends_on
        ):
            raise PlanningValidationError("depends_on must be a list of positive integers")
        if any(value == chapter_no for value in depends_on):
            raise PlanningValidationError("a chapter cannot depend on itself")
        chapters.append(
            {
                "chapter_no": chapter_no,
                "title": _text(raw.get("title"), "chapter.title"),
                "goal": _text(raw.get("goal"), "chapter.goal", required=True),
                "required_beats": _string_list(raw.get("required_beats"), "chapter.required_beats"),
                "forbidden_outcomes": _string_list(
                    raw.get("forbidden_outcomes"), "chapter.forbidden_outcomes"
                ),
                "depends_on": list(depends_on),
                "source_refs": list(raw.get("source_refs", []))
                if isinstance(raw.get("source_refs", []), list)
                else [],
            }
        )

    chapters.sort(key=lambda item: item["chapter_no"])
    expected_numbers = set(range(1, requested + 1))
    if {item["chapter_no"] for item in chapters} != expected_numbers:
        raise PlanningValidationError("chapter_no values must cover 1..target_chapter_count")

    return {
        "title": title,
        "logline": logline,
        "synopsis": synopsis,
        "genre": genre,
        "tone": tone,
        "themes": themes,
        "chapters": chapters,
    }


def build_outline_nodes(
    draft: Mapping[str, Any], *, book_id: str, outline_version_id: str
) -> list[dict[str, Any]]:
    """Build ORM-ready node dictionaries without mutating the database."""
    del book_id, outline_version_id
    nodes = []
    for chapter in draft["chapters"]:
        nodes.append(
            {
                "node_type": "chapter",
                "volume_no": 1,
                "chapter_no": chapter["chapter_no"],
                "title": chapter["title"],
                "goal": chapter["goal"],
                "required_beats": chapter["required_beats"],
                "forbidden_outcomes": chapter["forbidden_outcomes"],
                "involved_character_ids": [],
                "plot_thread_ids": [],
                "depends_on": [{"node_id": value} for value in chapter["depends_on"]],
                "expected_state_changes": [],
                "source_refs": chapter["source_refs"],
            }
        )
    return nodes
