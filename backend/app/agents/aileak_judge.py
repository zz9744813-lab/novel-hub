"""v7.4 AILeakJudgeAgent - Layer 2 semantic judgment on suspicious content.

C-24: Three-layer detection:
  Layer 0: Hard block (protocol/structure errors) - in leak_guard.py
  Layer 1: Regex prefilter - in leak_guard.py
  Layer 2: AILeakJudgeAgent (this file) - semantic judgment

Key rules:
- Does NOT read reasoning or raw response
- Only reads target paragraph + context + agent role + prefilter hits
- temperature = 0
- Uses model binding (agent_role=aileak_judge) when available
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

logger = logging.getLogger("novelforge.agents.aileak_judge")


SYSTEM_PROMPT = """你是 AI 元评论泄漏判定 Agent。

任务：判断目标段落中的可疑表达，是小说叙事、角色对白、作者式旁白，
还是模型分析、规划、自检、格式说明或生成过程元评论。

规则：
1. 只做分类，不重写正文。
2. 不得因成人、暴力、露骨程度或道德偏好判定泄漏。
3. 引号内角色对白必须结合上下文判断。
4. "以下是正文""我将开始""字数统计""这一段应该怎么写"等通常属于元评论。
5. confidence < 0.85 时返回 uncertain。
6. 输出只能是 JSON。"""


def build_judge_prompt(
    target_paragraph: str,
    prev_paragraph: str | None,
    next_paragraph: str | None,
    agent_role: str,
    prefilter_hits: list[dict],
) -> str:
    """Build user content for AILeakJudgeAgent."""
    return f"""目标段落：
{target_paragraph}

前一段（如有）：
{prev_paragraph or '（无）'}

后一段（如有）：
{next_paragraph or '（无）'}

Agent Role: {agent_role}
预筛命中项：{prefilter_hits}

请输出 JSON 判断结果，字段：
paragraph_id, classification, confidence, evidence_span, decision, reason, safe_to_remove_directly
classification ∈ fictional_narration|character_dialogue|authorial_meta|ai_meta_commentary|uncertain
decision ∈ allow|patch|block|human_review
"""


def _parse_judgment(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise json.JSONDecodeError("empty", text, 0)

    # Strip markdown fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Best-effort extract first JSON object
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        return json.loads(m.group(0))


async def _resolve_judge_model(book_id: uuid.UUID | None) -> tuple[str, str, str | None]:
    """Resolve aileak_judge binding; fall back to draft_writer/global only if missing."""
    from app.database import async_session_factory
    from app.v74_utils import ModelBindingService

    async with async_session_factory() as db:
        svc = ModelBindingService(db)
        if book_id:
            binding = await svc.get_binding("aileak_judge", book_id)
            if binding:
                return binding.provider, binding.primary_model, binding.fallback_model
        binding = await svc.get_binding("aileak_judge", book_id or uuid.UUID(int=0))
        if binding:
            return binding.provider, binding.primary_model, binding.fallback_model
        # Prefer a lightweight existing binding rather than inventing a model name
        for role in ("review_agent", "state_extractor", "draft_writer"):
            b = await svc.get_binding(role, book_id) if book_id else await svc.get_binding(role, uuid.UUID(int=0))
            if b:
                return b.provider, b.primary_model, b.fallback_model
    # Absolute last resort — still explicit, logged by caller
    return "new-api", "glm-5.2", None


async def run_aileak_judge(
    target_paragraph: str,
    prev_paragraph: str | None = None,
    next_paragraph: str | None = None,
    agent_role: str = "unknown",
    prefilter_hits: list[dict] | None = None,
    book_id: uuid.UUID | None = None,
    model_gateway: Any = None,  # legacy unused; kept for signature compat
) -> dict:
    """Execute AILeakJudgeAgent for Layer 2 semantic judgment.

    Returns decision dict. On failure: fail-closed to human_review (not allow).
    """
    from app.gateway.model_gateway import stream_with_retry

    user_content = build_judge_prompt(
        target_paragraph=target_paragraph,
        prev_paragraph=prev_paragraph,
        next_paragraph=next_paragraph,
        agent_role=agent_role,
        prefilter_hits=prefilter_hits or [],
    )

    try:
        provider, model, fallback_model = await _resolve_judge_model(book_id)
        result = await stream_with_retry(
            system_prompt=SYSTEM_PROMPT,
            user_content=user_content,
            model=model,
            temperature=0.0,
            max_tokens=1024,
            provider=provider,
            fallback_model=fallback_model,
        )

        if not result.final_content:
            return {
                "paragraph_id": "unknown",
                "classification": "uncertain",
                "confidence": 0.0,
                "decision": "human_review",
                "reason": "Empty response from judge",
                "evidence_span": target_paragraph[:80],
                "safe_to_remove_directly": False,
            }

        judgment = _parse_judgment(result.final_content)
        decision = judgment.get("decision", "human_review")
        if decision not in ["allow", "patch", "block", "human_review"]:
            decision = "human_review"

        return {
            "paragraph_id": judgment.get("paragraph_id", "unknown"),
            "classification": judgment.get("classification", "uncertain"),
            "confidence": float(judgment.get("confidence", 0.0)),
            "evidence_span": judgment.get("evidence_span", target_paragraph[:80]),
            "decision": decision,
            "reason": judgment.get("reason", ""),
            "safe_to_remove_directly": bool(judgment.get("safe_to_remove_directly", False)),
        }

    except json.JSONDecodeError as e:
        logger.warning(f"AILeakJudge JSON parse error: {e}")
        return {
            "paragraph_id": "unknown",
            "classification": "uncertain",
            "confidence": 0.0,
            "decision": "human_review",
            "reason": f"Failed to parse judge output: {e}",
            "evidence_span": target_paragraph[:80],
            "safe_to_remove_directly": False,
        }
    except Exception as e:
        logger.error(f"AILeakJudge error: {e}")
        return {
            "paragraph_id": "unknown",
            "classification": "uncertain",
            "confidence": 0.0,
            "decision": "human_review",
            "reason": f"Judge call failed: {e}",
            "evidence_span": target_paragraph[:80],
            "safe_to_remove_directly": False,
        }
