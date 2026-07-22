"""AILeakGuard - §11.8: three-layer detection of AI meta-commentary in final content."""
import re


# Layer 1: structural markers
STRUCTURE_PATTERNS = [
    (r'<think>', r''),
    (r'<analysis>', r'</analysis>'),
]

# Layer 2: meta-narrative patterns
META_PATTERNS = [
    r'现在开始写',
    r'先分析一下',
    r'需要注意',
    r'符合要求',
    r'字数大约',
    r'这一段应该',
    r'接下来描写',
    r'检查是否',
    r'作为 AI',
    r'作为AI',
    r'Let me think',
    r'Actually',
    r'Wait,',
    r'我将开始',
    r'以下是正文',
    r'以下是',
]

# Layer 3: suspicious paragraph classification (simplified)
SUSPICIOUS_CLASSIFICATIONS = {
    "ai_meta_commentary": 0.95,
    "planning_leak": 0.85,
    "reasoning_leak": 0.90,
    "tool_leak": 0.95,
    "word_count_leak": 0.80,
}


class LeakResult:
    def __init__(self):
        self.findings = []
        self.contamination_ratio = 0.0
        self.block_candidate = False
        self.inline_leak_count = 0


def check_leak(final_content: str) -> LeakResult:
    """Run three-layer leak detection on final_content."""
    result = LeakResult()
    if not final_content:
        return result

    paragraphs = final_content.split('\n\n')
    total_len = max(len(final_content), 1)
    contaminated_len = 0

    for i, para in enumerate(paragraphs):
        para_stripped = para.strip()
        if not para_stripped:
            continue

        para_id = f"p-{i:04d}"
        found = []

        # Layer 1: structural markers
        for open_tag, close_tag in STRUCTURE_PATTERNS:
            if re.search(open_tag, para_stripped):
                found.append({
                    "paragraph_id": para_id,
                    "classification": "reasoning_leak",
                    "confidence": 0.95,
                    "evidence_span": para_stripped[:100],
                    "safe_to_remove_directly": False,
                    "recommended_action": "block_and_retry",
                })
                result.inline_leak_count += 1

        # Layer 2: meta-narrative patterns
        for pattern in META_PATTERNS:
            if re.search(pattern, para_stripped, re.IGNORECASE):
                found.append({
                    "paragraph_id": para_id,
                    "classification": "ai_meta_commentary",
                    "confidence": 0.90,
                    "evidence_span": para_stripped[:100],
                    "safe_to_remove_directly": False,
                    "recommended_action": "local_patch",
                })
                result.inline_leak_count += 1
                break

        if found:
            result.findings.extend(found)
            contaminated_len += len(para_stripped)

    result.contamination_ratio = contaminated_len / total_len

    # Block conditions per §11.9
    if result.contamination_ratio > 0.10:
        result.block_candidate = True
    if result.inline_leak_count >= 3:
        result.block_candidate = True

    return result
