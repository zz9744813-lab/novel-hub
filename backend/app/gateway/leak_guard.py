"""AILeakGuard - v7.4 Three-layer detection of AI meta-commentary.

Layer 0: Hard block for protocol/structure errors (direct block, no LLM)
Layer 1: Regex prefilter (candidates only, no final decision)
Layer 2: AILeakJudgeAgent (semantic judgment)

C-24: Three-layer detection per spec.
"""
import re
import logging

logger = logging.getLogger("novelforge.leak_guard")

# Build thinking tags safely
_THINK_OPEN = chr(60) + "think" + chr(62)
_THINK_CLOSE = chr(60) + "/think" + chr(62)


# ═══════════════════════════════════════════════════════════════════════════════
# Layer 1: Regex prefilter patterns (candidates, not final)
# ═══════════════════════════════════════════════════════════════════════════════

META_PATTERNS = [
    r'现在开始写',
    r'先分析一下',
    r'下面是正文',
    r'需要注意',
    r'符合要求',
    r'字数大约',
    r'这一段应该',
    r'接下来描写',
    r'检查是否',
    r'作为AI',
    r'作为 AI',
    r'Let me think',
    r'Actually[,\s]',
    r'Wait[,\s]',
    r'我将开始',
    r'以下是正文',
]


class LeakResult:
    def __init__(self):
        self.findings: list[dict] = []
        self.layer0_block: bool = False
        self.layer0_reason: str | None = None
        self.layer1_candidates: list[dict] = []
        self.contamination_ratio: float = 0.0
        self.inline_leak_count: int = 0


def layer0_check(final_content: str, reasoning: str | None) -> tuple[bool, str]:
    """Layer 0: Hard block for protocol/structure errors.
    
    Returns (should_block, block_reason)
    Does NOT call LLM - pure structural check.
    """
    if not final_content:
        if reasoning:
            return True, "PROTOCOL_OR_STRUCTURE_LEAK: final empty with reasoning"
        return False, ""
    
    # Check for unclosed thinking tags
    open_count = len(re.findall(re.escape(_THINK_OPEN), final_content, re.IGNORECASE))
    close_count = len(re.findall(re.escape(_THINK_CLOSE), final_content, re.IGNORECASE))
    if open_count != close_count:
        return True, f"PROTOCOL_OR_STRUCTURE_LEAK: unclosed thinking tag"
    
    # Check for other unclosed structural markers
    for open_tag, close_tag in [(r'<analysis>', r'</analysis>'), (r'<reasoning>', r'</reasoning>')]:
        open_c = len(re.findall(open_tag, final_content, re.IGNORECASE))
        close_c = len(re.findall(close_tag, final_content, re.IGNORECASE))
        if open_c != close_c:
            return True, f"PROTOCOL_OR_STRUCTURE_LEAK: unclosed {open_tag}"
    
    # Check for tool/usage leakage in final
    tool_patterns = [r'"tool_calls"', r'"function"\s*:', r'"arguments"\s*:', r'"usage"\s*:\s*\{']
    for pattern in tool_patterns:
        if re.search(pattern, final_content):
            return True, f"PROTOCOL_OR_STRUCTURE_LEAK: tool/usage in final"
    
    return False, ""


def layer1_prefilter(final_content: str) -> list[dict]:
    """Layer 1: Regex prefilter - returns candidates, NOT final decision."""
    candidates = []
    
    paragraphs = final_content.split('\n\n')
    for i, para in enumerate(paragraphs):
        para_stripped = para.strip()
        if not para_stripped:
            continue
        
        for pattern in META_PATTERNS:
            if re.search(pattern, para_stripped, re.IGNORECASE):
                # Check if inside quotes (simple heuristic)
                quote_count = para_stripped.count('"') + para_stripped.count('"') + para_stripped.count('"')
                if quote_count % 2 == 1:
                    # Inside quotes - likely dialogue
                    continue
                
                candidates.append({
                    "paragraph_id": f"p-{i:04d}",
                    "pattern": pattern,
                    "span": para_stripped[:100],
                })
                break
    
    return candidates


def check_leak(final_content: str, reasoning: str | None = None) -> LeakResult:
    """Run three-layer leak detection.
    
    Layer 0: Hard block (immediate)
    Layer 1: Regex prefilter (candidates)
    Layer 2: Called separately via aileak_judge.run_aileak_judge()
    """
    result = LeakResult()
    
    if not final_content:
        return result
    
    # Layer 0
    should_block, block_reason = layer0_check(final_content, reasoning)
    if should_block:
        result.layer0_block = True
        result.layer0_reason = block_reason
        result.findings.append({
            "classification": "protocol_leak",
            "confidence": 0.99,
            "decision": "block",
            "reason": block_reason,
        })
        return result
    
    # Layer 1
    result.layer1_candidates = layer1_prefilter(final_content)
    
    # Calculate contamination
    total_len = max(len(final_content), 1)
    contaminated_len = sum(len(c.get("span", "")) for c in result.layer1_candidates)
    result.contamination_ratio = contaminated_len / total_len
    result.inline_leak_count = len(result.layer1_candidates)
    
    return result