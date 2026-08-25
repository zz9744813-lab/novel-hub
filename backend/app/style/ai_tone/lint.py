"""v9.7 AI-Tone Intelligence (spec §25–§26).

Diagnosis layer only: lint finds text patterns → ai_tone_findings; humans
confirm/dismiss/correct → book-scoped calibration; never auto-rewrites the
chapter and never injects into the Draft system prompt.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AIToneFinding, AIToneRuleCalibration

# First batch of absorbed rules (spec §25). NOT absolute: dashes/metaphor/etc excluded.
RULES = {
    "AITONE_REVERSAL_01": {
        "label": "翻案腔",
        "pattern": re.compile(r"他原本以为[^，。]{2,14}，[^。]{4,30}没想到|本以为是[^。]{4,20}，却"),
        "severity": "minor",
    },
    "AITONE_ISOMORPHISM_02": {
        "label": "机械同构",
        "pattern": re.compile(r"这不是[^。]{2,10}，这不是[^。]{2,10}"),
        "severity": "minor",
    },
    "AITONE_COMMENTARY_03": {
        "label": "空转评论",
        "pattern": re.compile(r"或许这就是[^。]{2,12}|也许，这就是[^。]{2,12}"),
        "severity": "minor",
    },
    "AITONE_TRANSLATION_04": {
        "label": "翻译腔",
        "pattern": re.compile(r"在(她|他|他们|我们)的(眼中|心里|世界里)|某种意义上(说)?，"),
        "severity": "minor",
    },
    "AITONE_COLON_05": {
        "label": "提示性冒号",
        "pattern": re.compile(r"——{2}[^。]{0,16}：|：[^。]{0,20}——"),
        "severity": "note",
    },
    "AITONE_NOMINAL_06": {
        "label": "抽象名词堆积",
        "pattern": re.compile(r"(焦虑|孤独|疏离|虚无|宿命)(与|和)(孤独|疏离|焦虑|虚无)"),
        "severity": "note",
    },
    "AITONE_LIST_PARALLELISM_07": {
        "label": "列表式排比",
        "pattern": re.compile(r"也许是[^。]{2,10}，也许是[^。]{2,10}，也许是[^。]{2,10}"),
        "severity": "note",
    },
}


def lint_text(text: str, *, book_id=None, chapter_id=None, chapter_run_id=None, scene_type=None) -> list[AIToneFinding]:
    """Pure lint: returns findings (not persisted here)."""
    out = []
    for rule_id, rule in RULES.items():
        for m in rule["pattern"].finditer(text):
            out.append(
                AIToneFinding(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chapter_id=chapter_id,
                    chapter_run_id=chapter_run_id,
                    paragraph_key=None,
                    start=m.start(),
                    end=m.end(),
                    excerpt=text[max(0, m.start() - 6): m.end() + 6],
                    rule_id=rule_id,
                    severity=rule["severity"],
                    confidence=0.85 if rule_id in ("AITONE_REVERSAL_01", "AITONE_TRANSLATION_04") else 0.6,
                    scene_type=scene_type,
                    style_override=False,
                    auto_patchable=False,
                )
            )
    return out


async def persist_findings(db: AsyncSession, findings: list[AIToneFinding]) -> int:
    for f in findings:
        db.add(f)
    return len(findings)


async def apply_human_disposition(
    db: AsyncSession,
    *,
    finding: AIToneFinding,
    disposition: str,  # confirmed | dismissed | corrected
    corrected_category: str | None = None,
) -> dict:
    """§26: human feedback calibrates the rule for THIS book only."""
    from sqlalchemy import select

    finding.human_disposition = disposition
    if corrected_category:
        finding.corrected_category = corrected_category

    cal = (
        await db.execute(
            select(AIToneRuleCalibration).where(
                AIToneRuleCalibration.book_id == finding.book_id,
                AIToneRuleCalibration.rule_id == finding.rule_id,
            )
        )
    ).scalar_one_or_none()
    if cal is None:
        cal = AIToneRuleCalibration(
            id=uuid.uuid4(), book_id=finding.book_id, rule_id=finding.rule_id
        )
        db.add(cal)

    if disposition == "confirmed":
        cal.confirmed = (cal.confirmed or 0) + 1
    elif disposition == "dismissed":
        cal.dismissed = (cal.dismissed or 0) + 1
    elif disposition == "corrected":
        cal.corrected = (cal.corrected or 0) + 1
    total = (cal.confirmed or 0) + (cal.dismissed or 0) + (cal.corrected or 0)
    cal.precision = round((cal.confirmed or 0) / total, 3) if total else None
    # book-specific weight: repeatedly dismissed rules lose weight locally only
    if (cal.dismissed or 0) >= 3 and total >= 5:
        cal.weight = round(max(0.2, 1.0 - 0.15 * ((cal.dismissed or 0) - 2)), 2)
    else:
        cal.weight = 1.0
    await db.flush()
    return {"precision": cal.precision, "weight": cal.weight, "disposition": disposition}
