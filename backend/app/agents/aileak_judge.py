"""v7.4 AILeakJudgeAgent - Layer 2 semantic judgment on suspicious content.

C-24: Three-layer detection:
  Layer 0: Hard block (protocol/structure errors) - in leak_guard.py
  Layer 1: Regex prefilter - in leak_guard.py  
  Layer 2: AILeakJudgeAgent (this file) - semantic judgment

Key rules:
- Does NOT read reasoning or raw response
- Only reads target paragraph + context + agent role + prefilter hits
- temperature = 0, response_format = JSON Schema
"""
import json
import logging
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

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "paragraph_id": {"type": "string"},
        "classification": {
            "type": "string",
            "enum": ["fictional_narration", "character_dialogue", "authorial_meta", "ai_meta_commentary", "uncertain"]
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence_span": {"type": "string"},
        "decision": {"type": "string", "enum": ["allow", "patch", "block", "human_review"]},
        "reason": {"type": "string"},
        "safe_to_remove_directly": {"type": "boolean"},
    },
    "required": ["paragraph_id", "classification", "confidence", "decision", "reason"],
}


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

请输出 JSON 判断结果。"""


async def run_aileak_judge(
    model_gateway,  # Injected: has stream_with_retry method
    target_paragraph: str,
    prev_paragraph: str | None = None,
    next_paragraph: str | None = None,
    agent_role: str = "unknown",
    prefilter_hits: list[dict] | None = None,
) -> dict:
    """Execute AILeakJudgeAgent for Layer 2 semantic judgment.
    
    Returns:
        {
            "classification": str,
            "confidence": float,
            "decision": str,  # 'allow', 'patch', 'block', 'human_review'
            "reason": str,
            ...
        }
    """
    user_content = build_judge_prompt(
        target_paragraph=target_paragraph,
        prev_paragraph=prev_paragraph,
        next_paragraph=next_paragraph,
        agent_role=agent_role,
        prefilter_hits=prefilter_hits or [],
    )
    
    try:
        result = await model_gateway.stream_with_retry(
            system_prompt=SYSTEM_PROMPT,
            user_content=user_content,
            model="deepseek-v4-flash",  # Fixed lightweight model for judge
            temperature=0.0,
        )
        
        if not result.final_content:
            return {
                "classification": "uncertain",
                "confidence": 0.0,
                "decision": "human_review",
                "reason": "Empty response from judge",
            }
        
        # Parse JSON
        judgment = json.loads(result.final_content)
        
        # Validate decision
        decision = judgment.get("decision", "human_review")
        if decision not in ["allow", "patch", "block", "human_review"]:
            decision = "human_review"
        
        return {
            "paragraph_id": judgment.get("paragraph_id", "unknown"),
            "classification": judgment.get("classification", "uncertain"),
            "confidence": float(judgment.get("confidence", 0.0)),
            "evidence_span": judgment.get("evidence_span", ""),
            "decision": decision,
            "reason": judgment.get("reason", ""),
            "safe_to_remove_directly": judgment.get("safe_to_remove_directly", False),
        }
        
    except json.JSONDecodeError as e:
        logger.warning(f"AILeakJudge JSON parse error: {e}")
        return {
            "classification": "uncertain",
            "confidence": 0.0,
            "decision": "human_review",
            "reason": f"Failed to parse judge output: {e}",
        }
    except Exception as e:
        logger.error(f"AILeakJudge error: {e}")
        return {
            "classification": "uncertain",
            "confidence": 0.0,
            "decision": "human_review",
            "reason": f"Judge call failed: {e}",
        }
