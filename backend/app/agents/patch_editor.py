"""LocalRewriteEditorAgent - fixes specific paragraphs.
Per §8 + §A.5 v7.3. 3-round rule per issue cluster.

P0-03: no DB session during LLM.
"""
from __future__ import annotations

import uuid
import json
import hashlib
import logging
from app.agents.caller import call_agent

logger = logging.getLogger("novelforge.patch")


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def generate_patch(
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    issue: dict,
    chapter_content: str,
    voice_cards: list[dict] | None = None,
    tone_anchor: dict | None = None,
    retry_round: int = 1,
    **_deprecated,
) -> dict | None:
    paragraphs = chapter_content.split("\n\n")
    para_id = issue.get("paragraph_id", "p-0000")
    try:
        para_idx = int(para_id.split("-")[1])
    except (IndexError, ValueError):
        para_idx = 0

    target = paragraphs[para_idx] if para_idx < len(paragraphs) else ""
    before = "\n\n".join(paragraphs[:para_idx])[-500:] if para_idx > 0 else ""
    after = "\n\n".join(paragraphs[para_idx + 1 :])[:500] if para_idx + 1 < len(paragraphs) else ""
    expected_hash = compute_hash(target)

    user_content = json.dumps(
        {
            "target_paragraph": target,
            "context_before": before,
            "context_after": after,
            "review_issue": issue,
            "protected_facts": issue.get("protected_facts", []),
            "voice_cards": voice_cards or [],
            "tone_anchor": tone_anchor or {},
            "expected_hash": expected_hash,
            "scene_id": issue.get("scene_id"),
            "paragraph_key": para_id,
            "retry_round": retry_round,
        },
        ensure_ascii=False,
    )

    run, result, meta = await call_agent(
        book_id=book_id,
        agent_role="local_rewrite_editor",
        user_content=user_content,
        chapter_id=chapter_id,
    )

    if not result or not isinstance(result, dict):
        logger.error(f"PatchEditor failed for issue {issue.get('issue_id')}: {meta}")
        return None

    return {
        "replacement_text": result.get("replacement_text", ""),
        "expected_hash": expected_hash,
        "paragraph_key": para_id,
        "scene_id": issue.get("scene_id"),
        "resolved_issue_ids": result.get("resolved_issue_ids", [issue.get("issue_id")]),
        "source_run_id": str(run.id) if run else None,
    }


async def apply_patches(chapter_content: str, patches: list[dict]) -> str:
    paragraphs = chapter_content.split("\n\n")
    for patch in patches:
        for i, para in enumerate(paragraphs):
            if compute_hash(para) == patch.get("expected_hash"):
                paragraphs[i] = patch["replacement_text"]
                break
    return "\n\n".join(paragraphs)
