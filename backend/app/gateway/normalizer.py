"""Normalizer - §11.7 + v3.0 §9: final_content only; no soft-accept of broken JSON."""
from __future__ import annotations

import re
import json


def normalize_prose(final_content: str) -> str:
    """Normalize prose output: Unicode, strip markdown fences."""
    text = final_content.strip()
    text = re.sub(r"^```\w*\n", "", text)
    text = re.sub(r"\n```$", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _strip_fences(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:json|JSON)?\s*\n?", "", t)
    t = re.sub(r"\n?```\s*$", "", t)
    return t.strip()


def normalize_json(final_content: str) -> dict | None:
    """Parse JSON object/array. Fail closed — no trailing-comma auto-success.

    Allowed: markdown fence strip + direct json.loads of whole body or
    single balanced top-level object/array span. Rejects partial/malformed.
    """
    if not final_content or not str(final_content).strip():
        return None
    text = _strip_fences(final_content)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            return {"_array": obj}
        return None
    except json.JSONDecodeError:
        pass

    start_obj = text.find("{")
    start_arr = text.find("[")
    if start_obj == -1 and start_arr == -1:
        return None
    if start_obj != -1 and (start_arr == -1 or start_obj < start_arr):
        start, open_c, close_c = start_obj, "{", "}"
    else:
        start, open_c, close_c = start_arr, "[", "]"

    depth = 0
    in_str = False
    esc = False
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == open_c:
            depth += 1
        elif ch == close_c:
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None
    span = text[start : end + 1]
    try:
        obj = json.loads(span)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list):
        return {"_array": obj}
    return None


def check_truncation(final_content: str) -> bool:
    if not final_content:
        return False
    if len(final_content) < 10:
        return True
    text = final_content.rstrip()
    end_chars = set("。！？!?…”」』\n")
    if text and text[-1] not in end_chars:
        return True
    return False


def check_empty(final_content: str) -> bool:
    return not final_content or not final_content.strip()
