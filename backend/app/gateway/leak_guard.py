"""AILeakGuard - v7.4 Three-layer detection of AI meta-commentary.

Layer 0: Hard block for protocol/structure errors (direct block, no LLM)
Layer 1: Regex prefilter (candidates only, no final decision)
Layer 2: AILeakJudgeAgent (semantic judgment) - async, called from publish pipeline

C-24: Three-layer detection per spec.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("novelforge.leak_guard")

# Build thinking tags safely
_THINK_OPEN = chr(60) + "think" + chr(62)
_THINK_CLOSE = chr(60) + "/think" + chr(62)


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 1: Regex prefilter patterns (candidates, not final)
# ═══════════════════════════════════════════════════════════════════════════════

META_PATTERNS = [
    r"现在开始写",
    r"先分析一下",
    r"下面是正文",
    r"需要注意",
    r"符合要求",
    r"字数大约",
    r"这一段应该",
    r"接下来描写",
    r"检查是否",
    r"作为AI",
    r"作为 AI",
    r"Let me think",
    r"Actually[,\s]",
    r"Wait[,\s]",
    r"我将开始",
    r"以下是正文",
]


@dataclass
class LeakResult:
    findings: list[dict] = field(default_factory=list)
    layer0_block: bool = False
    layer0_reason: str | None = None
    layer1_candidates: list[dict] = field(default_factory=list)
    layer2_judgments: list[dict] = field(default_factory=list)
    contamination_ratio: float = 0.0
    inline_leak_count: int = 0
    # Convenience flag used by publish_pipeline / callers
    block_candidate: bool = False
    block_reason: str | None = None


def layer0_check(final_content: str, reasoning: str | None) -> tuple[bool, str]:
    """Layer 0: Hard block for protocol/structure errors.

    Returns (should_block, block_reason)
    Does NOT call LLM - pure structural check.
    """
    if not final_content:
        if reasoning:
            return True, "PROTOCOL_OR_STRUCTURE_LEAK: final empty with reasoning"
        return False, ""

    open_count = len(re.findall(re.escape(_THINK_OPEN), final_content, re.IGNORECASE))
    close_count = len(re.findall(re.escape(_THINK_CLOSE), final_content, re.IGNORECASE))
    if open_count != close_count:
        return True, "PROTOCOL_OR_STRUCTURE_LEAK: unclosed thinking tag"

    for open_tag, close_tag in [(r"<analysis>", r"</analysis>"), (r"<reasoning>", r"</reasoning>")]:
        open_c = len(re.findall(open_tag, final_content, re.IGNORECASE))
        close_c = len(re.findall(close_tag, final_content, re.IGNORECASE))
        if open_c != close_c:
            return True, f"PROTOCOL_OR_STRUCTURE_LEAK: unclosed {open_tag}"

    tool_patterns = [r'"tool_calls"', r'"function"\s*:', r'"arguments"\s*:', r'"usage"\s*:\s*\{']
    for pattern in tool_patterns:
        if re.search(pattern, final_content):
            return True, "PROTOCOL_OR_STRUCTURE_LEAK: tool/usage in final"

    return False, ""


def layer1_prefilter(final_content: str) -> list[dict]:
    """Layer 1: Regex prefilter - returns candidates, NOT final decision."""
    candidates = []

    paragraphs = final_content.split("\n\n")
    for i, para in enumerate(paragraphs):
        para_stripped = para.strip()
        if not para_stripped:
            continue

        for pattern in META_PATTERNS:
            if re.search(pattern, para_stripped, re.IGNORECASE):
                quote_count = (
                    para_stripped.count('"')
                    + para_stripped.count("“")
                    + para_stripped.count("”")
                )
                if quote_count % 2 == 1:
                    # Inside quotes - likely dialogue
                    continue

                candidates.append({
                    "paragraph_id": f"p-{i:04d}",
                    "paragraph_index": i,
                    "pattern": pattern,
                    "span": para_stripped[:100],
                    "text": para_stripped,
                })
                break

    return candidates


def check_leak(final_content: str, reasoning: str | None = None) -> LeakResult:
    """Run Layer 0 + Layer 1 leak detection (sync).

    Layer 2 is async and invoked via check_leak_async() / full_pipeline_async().
    """
    result = LeakResult()

    if not final_content:
        return result

    # Layer 0
    should_block, block_reason = layer0_check(final_content, reasoning)
    if should_block:
        result.layer0_block = True
        result.layer0_reason = block_reason
        result.block_candidate = True
        result.block_reason = block_reason
        result.findings.append({
            "classification": "protocol_leak",
            "confidence": 0.99,
            "decision": "block",
            "reason": block_reason,
            "evidence_span": final_content[:80],
        })
        return result

    # Layer 1
    result.layer1_candidates = layer1_prefilter(final_content)

    total_len = max(len(final_content), 1)
    contaminated_len = sum(len(c.get("text") or c.get("span", "")) for c in result.layer1_candidates)
    result.contamination_ratio = contaminated_len / total_len
    result.inline_leak_count = len(result.layer1_candidates)

    # Layer-1 hard thresholds (no LLM needed)
    if result.contamination_ratio > 0.10 or result.inline_leak_count >= 3:
        result.block_candidate = True
        result.block_reason = "leak_detected"
        for c in result.layer1_candidates:
            result.findings.append({
                "classification": "ai_meta_commentary",
                "confidence": 0.9,
                "decision": "block",
                "reason": f"layer1_hit:{c.get('pattern')}",
                "evidence_span": c.get("span", "")[:80],
            })

    return result


async def check_leak_async(
    final_content: str,
    reasoning: str | None = None,
    agent_role: str = "draft_writer",
    book_id=None,
) -> LeakResult:
    """Full three-layer check: Layer0+1 sync, Layer2 AILeakJudge when candidates exist.

    Fail-closed on Layer2 errors when candidates already exist.
    """
    result = check_leak(final_content, reasoning)
    if result.block_candidate or result.layer0_block:
        return result

    if not result.layer1_candidates:
        return result

    # Layer 2: semantic judge on each candidate paragraph
    from app.agents.aileak_judge import run_aileak_judge

    paragraphs = final_content.split("\n\n")
    block_votes = 0
    patch_votes = 0

    for cand in result.layer1_candidates[:8]:  # cap cost on tiny VPS
        idx = cand.get("paragraph_index", 0)
        prev_p = paragraphs[idx - 1].strip() if idx > 0 else None
        next_p = paragraphs[idx + 1].strip() if idx + 1 < len(paragraphs) else None

        judgment = await run_aileak_judge(
            target_paragraph=cand.get("text") or cand.get("span", ""),
            prev_paragraph=prev_p,
            next_paragraph=next_p,
            agent_role=agent_role,
            prefilter_hits=[{"pattern": cand.get("pattern"), "span": cand.get("span")}],
            book_id=book_id,
        )
        result.layer2_judgments.append(judgment)
        result.findings.append({
            "classification": judgment.get("classification", "uncertain"),
            "confidence": judgment.get("confidence", 0.0),
            "decision": judgment.get("decision", "human_review"),
            "reason": judgment.get("reason", ""),
            "evidence_span": judgment.get("evidence_span") or cand.get("span", "")[:80],
        })

        decision = judgment.get("decision")
        if decision == "block":
            block_votes += 1
        elif decision in ("patch", "human_review"):
            patch_votes += 1
        elif decision == "allow" and judgment.get("classification") == "ai_meta_commentary":
            # Judge said allow but classified as meta — treat conservatively
            patch_votes += 1

    if block_votes > 0 or patch_votes >= 2:
        result.block_candidate = True
        result.block_reason = "leak_detected_layer2"
    elif result.contamination_ratio > 0.05 and patch_votes > 0:
        result.block_candidate = True
        result.block_reason = "leak_detected_layer2"

    return result
