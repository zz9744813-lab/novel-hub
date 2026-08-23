"""Style Intelligence Engine service (spec §21, §36, §52).

Deterministic layer: build a StyleProfile's metric vector + fingerprint from
reference text, and score a chapter's style distance against that profile.
The LLM (style_analyzer) fills the semantic dimensions separately.
"""
from __future__ import annotations

import gzip
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChapterStyleScore, ReferenceSample, SceneStyleContract, StyleProfile
from app.style.metrics import (
    compute_fingerprint,
    extract_style_metrics,
    fingerprint_distance,
)

logger = logging.getLogger("novelforge.style")

METRIC_ENGINE_VERSION = "1.0"
ANALYZER_VERSION = "style_analyzer_v2"


def stratified_sample(
    text: str,
    *,
    segment_chars: int = 2000,
    max_segments: int = 12,
) -> list[str]:
    """分层采样（开头/25%/50%/75%/结尾），避免只取开头（spec §36）。"""
    text = (text or "").strip()
    if not text:
        return []
    total = len(text)
    if total <= segment_chars * max_segments:
        out = []
        for i in range(0, total, segment_chars):
            seg = text[i : i + segment_chars].strip()
            if seg:
                out.append(seg)
        return out[:max_segments]

    positions = [0.0, 0.25, 0.5, 0.75, 0.95]
    out = []
    for pos in positions:
        start = int(total * pos)
        seg = text[start : start + segment_chars].strip()
        if seg:
            out.append(seg)
    return out


def _avg(values: list[float]) -> float:
    return sum(values) / max(len(values), 1)


def aggregate_metrics(metric_list: list[dict]) -> dict:
    """Average numeric leaf values across segments (deterministic aggregation)."""
    if not metric_list:
        return {"surface": {}, "rhythm": {}, "dialogue": {}, "emotion": {}, "meta": {}}

    def gather(section: str, field: str) -> list[float]:
        vals = []
        for m in metric_list:
            v = m.get(section, {}).get(field)
            if isinstance(v, (int, float)):
                vals.append(float(v))
        return vals

    out: dict = {"surface": {}, "rhythm": {}, "dialogue": {}, "emotion": {}}
    for section in out:
        for m in metric_list:
            for field in m.get(section, {}):
                if field not in out[section]:
                    vals = gather(section, field)
                    out[section][field] = round(_avg(vals), 4) if vals else 0.0
    out["meta"] = {
        "char_count": sum(m.get("meta", {}).get("char_count", 0) for m in metric_list),
        "segment_count": len(metric_list),
    }
    return out


def build_metric_ranges(metric_list: list[dict]) -> dict:
    """StyleMetricRange (§39): target ± preferred ± hard bounds per key field."""
    if not metric_list:
        return {}
    keys = ["dialogue.dialogue_ratio", "surface.sentence_chars_mean", "surface.lexical_diversity"]
    ranges: dict = {}
    for key in keys:
        section, field = key.split(".")
        vals = [
            float(m.get(section, {}).get(field))
            for m in metric_list
            if isinstance(m.get(section, {}).get(field), (int, float))
        ]
        if not vals:
            continue
        target = _avg(vals)
        spread = max(max(vals) - min(vals), target * 0.1, 0.01)
        ranges[key] = {
            "target": round(target, 4),
            "preferred_min": round(target - spread * 0.6, 4),
            "preferred_max": round(target + spread * 0.6, 4),
            "hard_min": round(max(0.0, target - spread * 2), 4),
            "hard_max": round(target + spread * 2, 4),
        }
    return ranges


def aggregate_multiple_references(refs: list[tuple[dict, float]]) -> dict:
    """Weighted metric aggregation across multiple references (spec §38).

    refs: list of (metric_vector, weight). Weights normalize internally.
    Returns {"metric_vector": merged, "conflicts": [...]} — a STYLE_CONFLICT is
    raised when a key dimension (dialogue ratio) spreads too far across sources.
    """
    if not refs:
        return {"metric_vector": {}, "conflicts": []}
    total_w = sum(w for _, w in refs) or 1.0

    fields: dict[str, set] = {}
    for mv, _ in refs:
        for section in ("surface", "rhythm", "dialogue", "emotion"):
            for f in mv.get(section, {}):
                fields.setdefault(section, set()).add(f)

    merged: dict = {s: {} for s in ("surface", "rhythm", "dialogue", "emotion")}
    for section, fset in fields.items():
        for f in fset:
            vals: list[tuple[float, float]] = []
            for mv, w in refs:
                v = mv.get(section, {}).get(f)
                if isinstance(v, (int, float)):
                    vals.append((float(v), w))
            if vals:
                weighted = sum(v * w for v, w in vals) / total_w
                merged[section][f] = round(weighted, 4)

    conflicts: list[dict] = []
    d_ratios = [
        mv.get("dialogue", {}).get("dialogue_ratio")
        for mv, _ in refs
        if isinstance(mv.get("dialogue", {}).get("dialogue_ratio"), (int, float))
    ]
    if len(d_ratios) >= 2 and (max(d_ratios) - min(d_ratios)) > 0.3:
        conflicts.append(
            {"dimension": "dialogue_ratio", "spread": round(max(d_ratios) - min(d_ratios), 3)}
        )

    return {"metric_vector": merged, "conflicts": conflicts}


def build_profile_payload(text: str, genre_hint: str | None = None) -> dict:
    """Deterministic StyleProfile v2 payload from reference text."""
    segments = stratified_sample(text)
    metric_list = [extract_style_metrics(s) for s in segments]
    merged = aggregate_metrics(metric_list)
    return {
        "metric_vector": merged,
        "metric_ranges": build_metric_ranges(metric_list),
        "fingerprint": compute_fingerprint(merged),
        "narrative_profile": {},
        "dialogue_profile": {},
        "rhythm_profile": {},
        "emotion_expression_profile": {},
        "technique_profile": {},
        "scene_mode_profiles": {},
        "confidence_by_dimension": {},
        "genre_hint": genre_hint,
        "segment_count": len(segments),
    }


async def load_reference_text(db: AsyncSession, book_id: uuid.UUID) -> str:
    """Read and concatenate a book's reference samples (gzip on disk)."""
    rows = (
        await db.execute(
            select(ReferenceSample).where(ReferenceSample.book_id == book_id)
        )
    ).scalars().all()
    parts: list[str] = []
    for r in rows:
        try:
            data = gzip.decompress(Path(r.storage_path).read_bytes()).decode("utf-8")
            parts.append(data)
        except Exception:
            continue
    return "\n\n".join(parts)


async def load_scene_style_contract(
    db: AsyncSession, book_id: uuid.UUID, scene_no: int
) -> dict | None:
    """Load a scene's style contract for DraftWriter (spec §45, §47). Best-effort."""
    row = (
        await db.execute(
            select(SceneStyleContract).where(
                SceneStyleContract.book_id == book_id,
                SceneStyleContract.scene_no == scene_no,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "scene_no": row.scene_no,
        "scene_mode": row.scene_mode,
        "targets": row.targets,
        "semantic": row.semantic,
        "avoid": row.avoid,
    }


async def get_latest_profile(db: AsyncSession, book_id: uuid.UUID) -> StyleProfile | None:
    """Latest StyleProfile for a book (shared by router + pipeline)."""
    return (
        await db.execute(
            select(StyleProfile)
            .where(StyleProfile.book_id == book_id)
            .order_by(StyleProfile.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def create_style_profile(
    db: AsyncSession,
    *,
    book_id: uuid.UUID,
    reference_text: str,
    genre_hint: str | None = None,
    run_llm: bool = True,
) -> StyleProfile:
    segments = stratified_sample(reference_text)
    payload = build_profile_payload(reference_text, genre_hint)
    latest = (
        await db.execute(
            select(StyleProfile)
            .where(StyleProfile.book_id == book_id)
            .order_by(StyleProfile.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    version = (latest.version + 1) if latest else 1

    profile = StyleProfile(
        book_id=book_id,
        version=version,
        status="draft",
        metric_vector=payload["metric_vector"],
        metric_ranges=payload["metric_ranges"],
        fingerprint=payload["fingerprint"],
        narrative_profile=payload["narrative_profile"],
        dialogue_profile=payload["dialogue_profile"],
        rhythm_profile=payload["rhythm_profile"],
        emotion_expression_profile=payload["emotion_expression_profile"],
        technique_profile=payload["technique_profile"],
        scene_mode_profiles=payload["scene_mode_profiles"],
        confidence_by_dimension=payload["confidence_by_dimension"],
        analyzer_version=ANALYZER_VERSION,
        metric_engine_version=METRIC_ENGINE_VERSION,
    )
    db.add(profile)
    await db.flush()

    # Semantic layer: dedicated style_analyzer agent (spec §43). Best-effort —
    # deterministic metrics are already persisted, LLM failure is non-fatal.
    if run_llm and segments:
        try:
            from app.agents.style_analyzer import run_style_analyzer

            result = await run_style_analyzer(
                book_id,
                segments=segments,
                deterministic_metrics=payload["metric_vector"],
                genre_hint=genre_hint,
            )
            if "error" not in result:
                profile.narrative_profile = result.get("narrative", {}) or {}
                profile.dialogue_profile = result.get("dialogue", {}) or {}
                profile.emotion_expression_profile = result.get("emotion_expression", {}) or {}
                profile.technique_profile = {
                    "techniques": result.get("techniques", []) or []
                }
                profile.scene_mode_profiles = result.get("scene_modes", {}) or {}
                profile.confidence_by_dimension = result.get("confidence_by_dimension", {}) or {}
                if result.get("warnings"):
                    profile.confidence_by_dimension = {
                        **profile.confidence_by_dimension,
                        "warnings": result["warnings"],
                    }
                profile.analyzer_version = f"{ANALYZER_VERSION}+llm"
        except Exception as e:  # pragma: no cover - LLM best-effort
            logger.warning("style_analyzer enrich failed book=%s: %s", book_id, e)

    return profile


def score_chapter_against_profile(content: str, profile: StyleProfile) -> dict:
    """Deterministic chapter style score + drift distance (spec §49, §52)."""
    metrics = extract_style_metrics(content)
    fp = compute_fingerprint(metrics)
    ref_fp = [float(x) for x in (profile.fingerprint or [])]

    distance = fingerprint_distance(fp, ref_fp) if ref_fp else float("inf")

    # Per-dimension sub-scores (heuristic: 1 / (1 + sub-distance))
    def sub_score(field_keys: list[str]) -> float:
        idx = {
            "sentence_chars_mean": 0,
            "sentence_chars_std": 1,
            "paragraph_chars_mean": 2,
            "commas_per_1000": 3,
            "periods_per_1000": 4,
            "exclamations_per_1000": 5,
            "ellipsis_ratio": 6,
            "lexical_diversity": 7,
            "sentence_length_cv": 8,
            "short_sentence_ratio": 9,
            "long_sentence_ratio": 10,
            "dialogue_ratio": 11,
            "speaker_switch_rate": 12,
            "explicit_emotion_word_ratio": 13,
            "body_signal_ratio": 14,
            "internal_monologue_ratio": 15,
        }
        d = 0.0
        for k in field_keys:
            i = idx[k]
            if i < len(fp) and i < len(ref_fp):
                d += (fp[i] - ref_fp[i]) ** 2
        return round(1 / (1 + (d ** 0.5)), 3)

    surface = sub_score(["sentence_chars_mean", "paragraph_chars_mean", "lexical_diversity"])
    rhythm = sub_score(["sentence_length_cv", "short_sentence_ratio", "long_sentence_ratio"])
    dialogue = sub_score(["dialogue_ratio", "speaker_switch_rate"])
    emotion = sub_score(["explicit_emotion_word_ratio", "body_signal_ratio", "internal_monologue_ratio"])
    narrative = round((surface + rhythm) / 2, 3)
    voice = round((dialogue + emotion) / 2, 3)
    overall = round((surface + rhythm + dialogue + emotion + narrative + voice) / 6, 3)

    return {
        "surface_score": surface,
        "rhythm_score": rhythm,
        "dialogue_score": dialogue,
        "narrative_score": narrative,
        "emotion_score": emotion,
        "voice_score": voice,
        "overall_score": overall,
        "distance_to_profile": distance,
        "metric_json": metrics,
    }


async def upsert_chapter_score(
    db: AsyncSession,
    *,
    book_id: uuid.UUID,
    chapter_no: int,
    content: str,
    profile: StyleProfile,
) -> ChapterStyleScore:
    result = score_chapter_against_profile(content, profile)
    row = (
        await db.execute(
            select(ChapterStyleScore).where(
                ChapterStyleScore.book_id == book_id,
                ChapterStyleScore.chapter_no == chapter_no,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = ChapterStyleScore(book_id=book_id, chapter_no=chapter_no)
        db.add(row)
    row.surface_score = result["surface_score"]
    row.rhythm_score = result["rhythm_score"]
    row.dialogue_score = result["dialogue_score"]
    row.narrative_score = result["narrative_score"]
    row.emotion_score = result["emotion_score"]
    row.voice_score = result["voice_score"]
    row.overall_score = result["overall_score"]
    row.distance_to_profile = result["distance_to_profile"]
    row.metric_json = result["metric_json"]
    return row
