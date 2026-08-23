"""Default novel rubric + policy bootstrap (spec §5, §6, §11, §13).

Every book gets a default policy (windowed, ahead=5) and the default
novel rubric on first access; both are overridable per book.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import EditorialReviewPolicy, EditorialRubricTemplate

# spec §11: default novel rubric, weights sum to 100
DEFAULT_RUBRIC_NAME = "默认小说评分表"
DEFAULT_RUBRIC: list[dict] = [
    {
        "key": "plot",
        "name": "剧情与章节目标",
        "weight": 20,
        "anchors": {
            "excellent": "章节完成了大纲目标且推进了主线冲突，无凑数事件",
            "pass": "目标基本完成，但推进力度或事件必要性偏弱",
            "poor": "章节偏离目标或原地踏步",
        },
    },
    {
        "key": "character",
        "name": "人物与动机",
        "weight": 20,
        "anchors": {
            "excellent": "人物行动由既有Belief/Goal驱动，并具有个体差异",
            "pass": "动机基本成立，但细部反应较模板化",
            "poor": "行动服务剧情而非人物自身逻辑",
        },
    },
    {
        "key": "causal",
        "name": "因果与连续性",
        "weight": 15,
        "anchors": {
            "excellent": "事件因果链完整，无未建立的前置知识或能力",
            "pass": "主线因果成立，个别依赖交代不足",
            "poor": "存在无因之果、非法知识或连续性断裂",
        },
    },
    {
        "key": "style",
        "name": "文风与表达",
        "weight": 15,
        "anchors": {
            "excellent": "文风统一且贴合VoiceCard，修辞准确有力",
            "pass": "文风基本稳定，偶有套路化表达",
            "poor": "文风漂移或堆砌辞藻",
        },
    },
    {
        "key": "pacing",
        "name": "节奏与张力",
        "weight": 10,
        "anchors": {
            "excellent": "张弛有度，场景转换干净，冲突升级清晰",
            "pass": "节奏总体可读，个别段落拖沓或跳跃",
            "poor": "通平或节奏失控",
        },
    },
    {
        "key": "dialogue",
        "name": "对白",
        "weight": 8,
        "anchors": {
            "excellent": "对白有潜台词与个体声口，不承担解说功能",
            "pass": "对白自然但信息密度偏低",
            "poor": "对白直白解释动机或千人一面",
        },
    },
    {
        "key": "immersion",
        "name": "沉浸感",
        "weight": 7,
        "anchors": {
            "excellent": "感官细节与视角纪律让场景可信可感",
            "pass": "沉浸基本成立，偶有视角漂移",
            "poor": "频繁跳视角或全无具体细节",
        },
    },
    {
        "key": "originality",
        "name": "原创性 / AI味",
        "weight": 5,
        "anchors": {
            "excellent": "表达新鲜，无AI腔或模板句式",
            "pass": "整体自然，少量可疑句式",
            "poor": "明显的AI套话、重复结构或总结腔",
        },
    },
]

RUBRIC_KEYS = [d["key"] for d in DEFAULT_RUBRIC]
RUBRIC_TOTAL = sum(d["weight"] for d in DEFAULT_RUBRIC)


def score_to_grade(score: int | float | None) -> str | None:
    """spec §13: A/B/C/D banding; None stays None."""
    if score is None:
        return None
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    return "D"


def validate_rubric_scores(scores: dict, rubric: list[dict]) -> int:
    """Validate per-dimension raw scores and return total points (0..100).

    Input: {key: raw_points} where raw_points is capped at the dimension
    weight. Unknown keys are rejected (typo protection, fail-closed).
    """
    known = {d["key"]: d["weight"] for d in rubric}
    unknown = set(scores) - set(known)
    if unknown:
        raise ValueError(f"UNKNOWN_RUBRIC_KEYS:{sorted(unknown)}")
    total = 0
    for key, raw in scores.items():
        if not isinstance(raw, (int, float)) or raw < 0:
            raise ValueError(f"INVALID_SCORE:{key}")
        total += min(float(raw), known[key])
    return int(round(total))


async def get_or_create_default_rubric(db: AsyncSession) -> EditorialRubricTemplate:
    row = (
        await db.execute(
            select(EditorialRubricTemplate).where(
                EditorialRubricTemplate.book_id.is_(None),
                EditorialRubricTemplate.name == DEFAULT_RUBRIC_NAME,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = EditorialRubricTemplate(
            book_id=None,
            name=DEFAULT_RUBRIC_NAME,
            dimensions=DEFAULT_RUBRIC,
            is_default=True,
        )
        db.add(row)
        await db.flush()
    return row


async def get_or_create_policy(db: AsyncSession, book_id) -> EditorialReviewPolicy:
    row = (
        await db.execute(
            select(EditorialReviewPolicy).where(EditorialReviewPolicy.book_id == book_id)
        )
    ).scalar_one_or_none()
    if row is None:
        rubric = await get_or_create_default_rubric(db)
        row = EditorialReviewPolicy(
            book_id=book_id,
            mode="windowed",
            max_unreviewed_ahead=5,
            review_sampling_mode="all",
            require_review=True,
            good_score_threshold=85,
            auto_pause_good_rate_threshold=60,
            auto_pause_consecutive_bad=2,
            rubric_template_id=rubric.id,
            experience_auto_activation=False,
            low_risk_auto_promote=False,
        )
        db.add(row)
        await db.flush()
    return row


async def resolve_rubric(db: AsyncSession, book_id) -> list[dict]:
    """Book override if present, else default template dimensions."""
    override = (
        await db.execute(
            select(EditorialRubricTemplate)
            .where(EditorialRubricTemplate.book_id == book_id)
            .order_by(EditorialRubricTemplate.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if override is not None:
        return override.dimensions
    return (await get_or_create_default_rubric(db)).dimensions
