"""Entity alias merge + import conflict detection (deterministic)."""
from __future__ import annotations

import re
import uuid
from typing import Any


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip().lower())


def resolve_characters(characters: list[dict]) -> tuple[list[dict], list[dict]]:
    """Merge alias-overlapping character candidates. Low confidence → conflict."""
    merged: list[dict] = []
    conflicts: list[dict] = []
    for c in characters or []:
        names = {_norm(c.get("canonical_name") or "")}
        for a in c.get("aliases") or []:
            names.add(_norm(a))
        names.discard("")
        hit = None
        for m in merged:
            mnames = set(m.get("_all_names") or [])
            if names & mnames:
                hit = m
                break
        if hit:
            # merge aliases
            hit.setdefault("aliases", [])
            for a in list(names):
                if a and a not in hit["_all_names"]:
                    hit["aliases"].append(a)
                    hit["_all_names"].add(a)
            if c.get("description") and not hit.get("description"):
                hit["description"] = c["description"]
            if c.get("role") and not hit.get("role"):
                hit["role"] = c["role"]
            # if both have different primary names of similar length — warn
            if _norm(c.get("canonical_name") or "") != _norm(hit.get("canonical_name") or ""):
                conflicts.append(
                    {
                        "code": "CHARACTER_ALIAS_MERGE",
                        "severity": "warning",
                        "entity_type": "character",
                        "entity_temp_id": hit.get("temp_id"),
                        "message": f"别名合并：{c.get('canonical_name')} → {hit.get('canonical_name')}",
                        "options": [
                            {"id": "accept_merge", "label": "接受合并"},
                            {"id": "keep_separate", "label": "保持独立（需人工拆分）"},
                        ],
                    }
                )
        else:
            item = dict(c)
            item["temp_id"] = item.get("temp_id") or f"temp-char-{len(merged)+1:03d}"
            item["_all_names"] = names
            if not item.get("aliases"):
                item["aliases"] = []
            merged.append(item)

    for m in merged:
        m.pop("_all_names", None)
    return merged, conflicts


def detect_outline_conflicts(
    volumes: list[dict],
    chapters: list[dict],
    declared_total: int | None,
) -> list[dict]:
    out: list[dict] = []
    ch_nos = sorted({int(c["chapter_no"]) for c in chapters if c.get("chapter_no") is not None})
    if not ch_nos and not volumes:
        return out

    # duplicates
    seen = set()
    for c in chapters:
        n = c.get("chapter_no")
        if n in seen:
            out.append(
                {
                    "code": "CHAPTER_NO_DUPLICATE",
                    "severity": "blocking",
                    "entity_type": "outline_chapter",
                    "entity_temp_id": str(n),
                    "message": f"章节编号重复：{n}",
                    "options": [
                        {"id": "keep_first", "label": "保留首次出现"},
                        {"id": "manual", "label": "稍后人工处理"},
                    ],
                }
            )
        seen.add(n)

    # gaps
    gaps = []
    for a, b in zip(ch_nos, ch_nos[1:]):
        if b > a + 1:
            gaps.append((a + 1, b - 1))
    if gaps:
        out.append(
            {
                "code": "CHAPTER_NO_GAP",
                "severity": "warning",
                "entity_type": "outline_chapter",
                "message": f"显式章节编号存在空缺：{gaps[:8]}",
                "options": [
                    {"id": "keep_detected", "label": "仅保留识别到的章节"},
                    {"id": "note_only", "label": "仅记录"},
                ],
            }
        )

    for v in volumes or []:
        vf, vt = v.get("chapter_from"), v.get("chapter_to")
        if vf is not None and vt is not None and ch_nos:
            in_vol = [n for n in ch_nos if vf <= n <= vt]
            if in_vol and (min(in_vol) != vf or max(in_vol) != vt):
                out.append(
                    {
                        "code": "VOLUME_RANGE_MISMATCH",
                        "severity": "warning",
                        "entity_type": "outline_volume",
                        "entity_temp_id": f"vol-{v.get('volume_no')}",
                        "message": (
                            f"第{v.get('volume_no')}卷声明 {vf}–{vt}，"
                            f"但显式章标题只识别到 {min(in_vol)}–{max(in_vol)}"
                        ),
                        "options": [
                            {"id": "use_declared", "label": "保留声明范围"},
                            {"id": "use_detected", "label": "采用识别范围"},
                            {"id": "keep_unresolved", "label": "暂不决定"},
                        ],
                    }
                )

    if declared_total and ch_nos and declared_total != len(ch_nos):
        out.append(
            {
                "code": "TOTAL_CHAPTERS_MISMATCH",
                "severity": "warning",
                "entity_type": "outline",
                "message": f"目标章节数 {declared_total} 与显式章纲数 {len(ch_nos)} 不一致（允许：声明总数可大于已写章纲）",
                "options": [
                    {"id": "use_declared", "label": "采用声明目标"},
                    {"id": "use_detected", "label": "采用识别数量"},
                ],
            }
        )
    return out


_CHAPTER_RE = re.compile(
    r"第\s*([0-9０-９]{1,4}|[一二三四五六七八九十百千两零〇]{1,6})\s*章\s*([^\n]{0,80})"
)
_VOLUME_RE = re.compile(
    r"第\s*([0-9０-９]{1,3}|[一二三四五六七八九十]{1,4})\s*卷\s*([^\n（(]{0,40})?"
    r"(?:[（(]\s*第?\s*(\d+)\s*[-–~到至]\s*(\d+)\s*章?\s*[）)])?"
)

_CN_NUM = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _parse_cn_int(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    # fullwidth digits
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if s.isdigit():
        return int(s)
    # simple 1-99 chinese
    if s in _CN_NUM:
        return _CN_NUM[s]
    if "十" in s:
        parts = s.split("十")
        left = _CN_NUM.get(parts[0], 1 if parts[0] == "" else None)
        right = _CN_NUM.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        if left is None:
            return None
        return left * 10 + right
    return None


def deterministic_outline_from_text(text: str) -> dict:
    """Fallback when LLM misses explicit 第N章 / 第N卷 patterns."""
    chapters: list[dict] = []
    seen = set()
    for m in _CHAPTER_RE.finditer(text or ""):
        n = _parse_cn_int(m.group(1))
        if not n or n in seen:
            continue
        seen.add(n)
        title = (m.group(2) or "").strip(" ：:.-—")
        chapters.append(
            {
                "chapter_no": n,
                "title": title or None,
                "goal": title or f"第{n}章",
                "volume_no": 1,
                "required_beats": [],
                "forbidden_outcomes": [],
                "source_heading": m.group(0)[:120],
            }
        )
    volumes: list[dict] = []
    for m in _VOLUME_RE.finditer(text or ""):
        vn = _parse_cn_int(m.group(1))
        if not vn:
            continue
        title = (m.group(2) or "").strip() or None
        cf = int(m.group(3)) if m.group(3) else None
        ct = int(m.group(4)) if m.group(4) else None
        volumes.append(
            {
                "volume_no": vn,
                "title": title,
                "chapter_from": cf,
                "chapter_to": ct,
                "goal": None,
                "themes": [],
            }
        )
    # assign volume_no to chapters by range
    for ch in chapters:
        for v in volumes:
            cf, ct = v.get("chapter_from"), v.get("chapter_to")
            if cf is not None and ct is not None and cf <= ch["chapter_no"] <= ct:
                ch["volume_no"] = v["volume_no"]
                break
    declared = None
    m = re.search(r"(?:目标|共|总计|一共)?\s*(\d{1,4})\s*章", text or "")
    if m:
        declared = int(m.group(1))
    return {
        "volumes": volumes,
        "chapters": sorted(chapters, key=lambda x: x["chapter_no"]),
        "declared_total_chapters": declared,
        "notes": ["deterministic_regex_fallback"] if chapters or volumes else [],
    }


def merge_outline(llm: dict | None, det: dict | None) -> dict:
    llm = llm or {}
    det = det or {}
    ch_map: dict[int, dict] = {}
    for c in det.get("chapters") or []:
        ch_map[int(c["chapter_no"])] = dict(c)
    for c in llm.get("chapters") or []:
        n = c.get("chapter_no")
        if n is None:
            continue
        n = int(n)
        base = ch_map.get(n, {})
        merged = {**base, **{k: v for k, v in c.items() if v not in (None, "", [])}}
        ch_map[n] = merged
    vol_map: dict[int, dict] = {}
    for v in det.get("volumes") or []:
        vol_map[int(v["volume_no"])] = dict(v)
    for v in llm.get("volumes") or []:
        n = int(v.get("volume_no") or 0)
        if not n:
            continue
        base = vol_map.get(n, {})
        vol_map[n] = {**base, **{k: v2 for k, v2 in v.items() if v2 not in (None, "", [])}}
    return {
        "volumes": [vol_map[k] for k in sorted(vol_map)],
        "chapters": [ch_map[k] for k in sorted(ch_map)],
        "declared_total_chapters": llm.get("declared_total_chapters")
        or det.get("declared_total_chapters"),
        "notes": list(dict.fromkeys((llm.get("notes") or []) + (det.get("notes") or []))),
    }

def build_preview_bundle(
    *,
    metadata: dict | None,
    world: dict | None,
    characters: list[dict],
    relationships: list[dict],
    outline: dict | None,
    plots: dict | None,
    writing_rules: list[dict],
    conflicts: list[dict],
    classify: dict | None,
) -> dict:
    outline = outline or {}
    volumes = outline.get("volumes") or []
    chapters = outline.get("chapters") or []
    meta = metadata or {}
    return {
        "title_guess": meta.get("title") or "未命名小说",
        "metadata": meta,
        "world": world or {},
        "characters": characters,
        "relationships": relationships,
        "volumes": volumes,
        "chapters": chapters,
        "plot_threads": (plots or {}).get("threads") or [],
        "writing_rules": writing_rules,
        "document_types": (classify or {}).get("document_types") or [],
        "primary_document_type": (classify or {}).get("primary_type"),
        "counts": {
            "作品信息": 1 if meta else 0,
            "世界设定": len((world or {}).get("rules") or []),
            "人物卡": len(characters),
            "人物关系": len(relationships),
            "地点": len((world or {}).get("locations") or []),
            "卷纲": len(volumes),
            "章纲": len(chapters),
            "剧情线": len((plots or {}).get("threads") or []),
            "写作规则": len(writing_rules),
            "待确认冲突": sum(1 for c in conflicts if c.get("status", "open") == "open"),
        },
        "note": "Phase2 LLM 抽取结果，确认前不会创建正式 Book。",
    }
