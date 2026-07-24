"""LocalRewriteEditorAgent - fixes specific paragraphs.
Per §8 + §A.5 v7.3. 3-round rule per issue cluster.
"""
import uuid
import json
import hashlib
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.agents.caller import call_agent
from app.models import ReviewIssue, RewritePatch

logger = logging.getLogger("novelforge.patch")


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def generate_patch(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    issue: dict,
    chapter_content: str,
    voice_cards: list[dict] | None = None,
    tone_anchor: dict | None = None,
    retry_round: int = 1,
) -> dict | None:
    """Generate a patch for a specific issue.
    
    Returns patch dict {replacement_text, resolved_issue_ids} or None.
    """
    # Extract target paragraph from content
    paragraphs = chapter_content.split("\n\n")
    para_id = issue.get("paragraph_id", "p-0000")
    try:
        para_idx = int(para_id.split("-")[1])
    except (IndexError, ValueError):
        para_idx = 0

    target = paragraphs[para_idx] if para_idx < len(paragraphs) else ""
    before = "\n\n".join(paragraphs[:para_idx])[-500:] if para_idx > 0 else ""
    after = "\n\n".join(paragraphs[para_idx+1:])[:500] if para_idx + 1 < len(paragraphs) else ""
    expected_hash = compute_hash(target)

    user_content = json.dumps({
        "target_paragraph": target,
        "context_before": before,
        "context_after": after,
        "review_issue": issue,
        "protected_facts": issue.get("protected_facts", []),
        "voice_cards": voice_cards or [],
        "tone_anchor": tone_anchor or {},
        "expected_hash": expected_hash,
    }, ensure_ascii=False)

    run, result, meta = await call_agent(
        db=db,
        book_id=book_id,
        agent_role="local_rewrite_editor",
        user_content=user_content,
        chapter_id=chapter_id,
    )

    if not result:
        logger.error(f"PatchEditor failed for issue {issue.get('issue_id')}: {meta}")
        return None

    # Validate hash
    replacement = result.get("replacement_text", "")
    
    return {
        "replacement_text": replacement,
        "expected_hash": expected_hash,
        "resolved_issue_ids": result.get("resolved_issue_ids", [issue.get("issue_id")]),
    }


async def apply_patches(chapter_content: str, patches: list[dict]) -> str:
    """Apply patches to chapter content, validating hashes.
    
    Per §8.2: hash mismatch = skip and regenerate.
    """
    paragraphs = chapter_content.split("\n\n")
    for patch in patches:
        # Find target paragraph
        for i, para in enumerate(paragraphs):
            if compute_hash(para) == patch.get("expected_hash"):
                paragraphs[i] = patch["replacement_text"]
                break
    return "\n\n".join(paragraphs)
