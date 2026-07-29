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


def deterministic_world_from_text(text: str) -> dict:
    """Regex/heuristic world rules + locations when LLM returns empty."""
    rules: list[dict] = []
    locations: list[dict] = []
    seen_rules: set[str] = set()
    seen_loc: set[str] = set()

    def add_loc(name: str, description: str | None = None) -> None:
        name = (name or "").strip().strip("。.;；")
        if not name or name in seen_loc or not (1 < len(name) <= 30):
            return
        # skip pure rule-ish phrases
        if re.search(r"不可|禁止|违者|必须|规则", name):
            return
        seen_loc.add(name)
        locations.append(
            {
                "name": name,
                "description": description,
                "aliases": [],
                "rules": [],
            }
        )

    def add_rule(body: str, hard: bool | None = None) -> None:
        body = (body or "").strip()
        if len(body) < 4:
            return
        # location-only lines are not rules
        if re.match(r"^地点[:：]", body):
            return
        key = re.sub(r"\s+", "", body)[:40]
        if key in seen_rules:
            return
        seen_rules.add(key)
        is_hard = bool(re.search(r"不可|禁止|违者|必须", body)) if hard is None else hard
        rules.append(
            {
                "rule_key": f"rule_{len(rules)+1}",
                "description": body,
                "category": "hard" if is_hard else "setting",
                "is_hard": is_hard,
            }
        )

    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    rule_ctx = False
    loc_ctx = False
    for ln in lines:
        if re.search(r"^(##+\s*)?(世界观|世界设定|设定|规则|硬规则|法则)", ln):
            rule_ctx = True
            loc_ctx = bool(re.search(r"地点|地理|城|镇|区域", ln))
            continue
        if re.search(r"^(##+\s*)?(地点|地理|场景|主要地点)", ln):
            loc_ctx = True
            rule_ctx = False
            continue
        if re.match(r"^##+\s+", ln) and not re.search(r"世界|设定|规则|地点|地理", ln):
            rule_ctx = False
            loc_ctx = False

        # "地点：A、B、C" anywhere
        m4 = re.search(r"地点[:：]\s*(.+)$", ln)
        if m4:
            parts = re.split(r"[、,，/|]", m4.group(1))
            for p in parts:
                add_loc(p)
            # strip trailing punctuation junk
            continue

        # bare location-like tokens in bullets: X城/Y谷/Z渊
        m5 = re.match(
            r"^[-*•·]\s*([^\s：:]{2,20}(?:城|镇|谷|渊|山|府|宫|殿|市|村|岛|界|域|宅))",
            ln,
        )
        if m5:
            add_loc(m5.group(1), ln.lstrip("-*•· ").strip())

        # explicit rule bullets
        m = re.match(r"^[-*•·]\s*(?:规则[:：]?\s*)?(.+)$", ln)
        if m and (rule_ctx or "规则" in ln or "不可" in ln or "禁止" in ln or "违者" in ln):
            body = m.group(1).strip()
            if re.match(r"^地点[:：]", body):
                continue
            # setting bullets under 世界观 that look like places: skip as rule if pure place line
            if re.match(r"^[^：:]{2,20}(?:城|镇|谷|渊|山|府|宫|殿|市|村|岛|界|域|宅)\s*$", body):
                add_loc(body)
                continue
            add_rule(body)
            continue

        # "规则：xxx" inline
        m2 = re.search(r"规则[:：]\s*(.+)$", ln)
        if m2:
            add_rule(m2.group(1).strip(), hard=True)

        if loc_ctx:
            m3 = re.match(r"^[-*•·]\s*(.+)$", ln)
            if m3:
                name = re.split(r"[：:，,。\s]", m3.group(1).strip())[0]
                add_loc(name, m3.group(1).strip())

    # world summary: first non-heading paragraph under 世界观 if any
    summary = None
    for i, ln in enumerate(lines):
        if re.search(r"世界观|世界设定", ln):
            for j in range(i + 1, min(i + 6, len(lines))):
                if lines[j].startswith("#") or lines[j].startswith("-"):
                    if lines[j].startswith("-") and not summary:
                        summary = lines[j].lstrip("-*•· ").strip()
                    break
                if len(lines[j]) > 8:
                    summary = lines[j]
                    break
            break

    return {
        "world_summary": summary,
        "rules": rules,
        "locations": locations,
        "notes": ["deterministic_world_fallback"] if rules or locations else [],
    }



def merge_world(llm: dict | None, det: dict | None) -> dict:
    llm = llm or {}
    det = det or {}
    rules: list[dict] = []
    seen_r: set[str] = set()
    for src in (llm.get("rules") or [], det.get("rules") or []):
        for r in src:
            key = (r.get("rule_key") or r.get("description") or "")[:80]
            if not key or key in seen_r:
                continue
            seen_r.add(key)
            rules.append(r)
    locs: list[dict] = []
    seen_l: set[str] = set()
    for src in (llm.get("locations") or [], det.get("locations") or []):
        for loc in src:
            name = (loc.get("name") or "").strip()
            if not name or name in seen_l:
                continue
            seen_l.add(name)
            locs.append(loc)
    return {
        "world_summary": llm.get("world_summary") or det.get("world_summary"),
        "rules": rules,
        "locations": locs,
        "notes": list(dict.fromkeys((llm.get("notes") or []) + (det.get("notes") or []))),
    }


def deterministic_metadata_from_text(text: str) -> dict:
    """Heuristic title/logline/genre/tags when LLM metadata is sparse."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    title = None
    for ln in lines[:12]:
        m = re.match(r"^#\s*《([^》]+)》", ln)
        if m:
            title = m.group(1).strip()
            break
        m = re.match(r"^#\s+(.+)$", ln)
        if m and "企划" not in m.group(1):
            title = re.sub(r"^《|》$", "", m.group(1).strip())
            break
        m = re.search(r"《([^》]+)》", ln)
        if m:
            title = m.group(1).strip()
            break

    def _section_body(headers: list[str], max_lines: int = 6, join: str = "；") -> str:
        for i, ln in enumerate(lines):
            # header line itself
            pure = re.sub(r"^#{1,6}\s*", "", ln).strip()
            low = pure.lower().replace(" ", "")
            if any(h in pure or h in low for h in headers):
                buf = []
                for nxt in lines[i + 1 : i + 1 + max_lines]:
                    if nxt.startswith("#") or re.match(r"^#{1,3}\s", nxt):
                        break
                    # stop at next labeled section-ish line
                    if re.match(r"^(类型|世界观|人物|卷纲|章纲|写作|文风|调性)", nxt):
                        break
                    cleaned = re.sub(r"^[-*•]\s*", "", nxt).strip()
                    # drop block-id noise like [b-000003]
                    cleaned = re.sub(r"\[b-\d+\]\s*", "", cleaned).strip()
                    if cleaned:
                        buf.append(cleaned)
                return join.join(buf).strip(join + " ")
        return ""

    # logline: prefer single first bullet/sentence under 一句话卖点
    logline = _section_body(["一句话卖点", "logline"], 3, join=" ")
    if logline:
        # keep first sentence-ish chunk only
        logline = re.split(r"[。！？\n]", logline)[0].strip()[:200]
    if not logline:
        for ln in lines[1:20]:
            if ln.startswith("#"):
                continue
            if re.match(r"^(类型|世界观|人物|卷纲|章纲|写作)", ln):
                continue
            cleaned = re.sub(r"\[b-\d+\]\s*", "", ln).strip()
            if len(cleaned) >= 6 and "卖点" not in cleaned:
                logline = cleaned[:200]
                break

    genre = None
    gbody = _section_body(["类型与体量", "类型", "genre"], 4)
    for cand in ("玄幻", "仙侠", "都市", "科幻", "言情", "历史", "悬疑", "末世", "无限流", "克苏鲁"):
        if cand in (gbody or text[:2000]):
            genre = cand
            break

    planned = None
    m = re.search(r"(?:目标|计划|预计)\s*(\d{1,4})\s*章", text)
    if m:
        planned = int(m.group(1))
    else:
        m = re.search(r"(\d{1,4})\s*章", gbody or "")
        if m:
            planned = int(m.group(1))

    tone = _section_body(["文风", "调性", "tone", "写作要求"], 5)[:300] or None
    themes: list[str] = []
    if "慈父" in text:
        themes.append("慈父")
    if "渊" in text or "西荒" in text:
        themes.append("西荒")
    if "养女" in text:
        themes.append("养成")
    tags = list(dict.fromkeys(([genre] if genre else []) + themes))
    synopsis = _section_body(["世界观", "故事简介", "synopsis", "简介"], 8)[:800] or None

    return {
        "title": title,
        "logline": logline or None,
        "synopsis": synopsis,
        "genre": genre,
        "tags": tags,
        "tone": tone,
        "themes": themes,
        "planned_chapters": planned,
        "confidence": 0.55 if title or logline else 0.2,
        "source": "deterministic_metadata",
    }


def _clean_logline(s: str | None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\[b-\d+\]\s*", "", str(s)).strip()
    # drop multi-section dumps
    if "类型与体量" in s or "世界观" in s or s.count("；") >= 2:
        # keep first clause only
        s = re.split(r"[；;\n]", s)[0].strip()
    s = re.split(r"[。！？]", s)[0].strip()
    s = s[:200]
    return s or None


def merge_metadata(llm: dict | None, det: dict | None) -> dict:
    llm = llm or {}
    det = det or {}
    out = dict(det)
    for k, v in llm.items():
        if v is None or v == "" or v == []:
            continue
        out[k] = v
    for k in ("tags", "themes"):
        vals = []
        for src in (llm.get(k) or [], det.get(k) or []):
            for x in src:
                if x and x not in vals:
                    vals.append(x)
        if vals:
            out[k] = vals
    # prefer cleaner logline
    det_ll = _clean_logline(det.get("logline"))
    llm_ll = _clean_logline(llm.get("logline"))
    if det_ll and (not llm_ll or len(str(llm.get("logline") or "")) > len(det_ll) * 1.5 or "[b-" in str(llm.get("logline") or "")):
        out["logline"] = det_ll
    else:
        out["logline"] = llm_ll or det_ll
    if not out.get("logline") and out.get("tone"):
        out["logline"] = _clean_logline(str(out["tone"])[:200])
    if not out.get("genre") and out.get("tags"):
        out["genre"] = out["tags"][0]
    return out


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
    title = meta.get("title") or "未命名小说"
    return {
        "title_guess": title,
        "metadata": meta,
        "logline": meta.get("logline"),
        "genre": meta.get("genre"),
        "tags": meta.get("tags") or [],
        "world": world or {},
        "characters": characters,
        "relationships": relationships,
        "volumes": volumes,
        "chapters": chapters,
        "plot_threads": (plots or {}).get("threads") or [],
        "writing_rules": writing_rules,
        "document_types": (classify or {}).get("document_types") or [],
        "primary_document_type": (classify or {}).get("primary_type"),
        "declared_total": meta.get("planned_chapters") or outline.get("declared_total_chapters"),
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
