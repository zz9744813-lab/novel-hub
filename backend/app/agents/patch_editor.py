"""LocalRewriteEditorAgent - fixes specific paragraphs.
Per §8 + §A.5 v7.3. 3-round rule per issue cluster.

P0-03: no DB session during LLM.
"""
from __future__ import annotations

import uuid
import json
import re
import hashlib
import logging
from app.agents.caller import call_agent

logger = logging.getLogger("novelforge.patch")


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _coerce_patch_result(result, meta: dict | None = None) -> dict | None:
    """Recover patch payload when model returns prose or fenced JSON."""
    if isinstance(result, dict):
        if result.get("replacement_text"):
            return result
        # sometimes nested
        for key in ("patch", "result", "data"):
            if isinstance(result.get(key), dict) and result[key].get("replacement_text"):
                return result[key]
        return result if result else None

    text = ""
    if isinstance(result, str):
        text = result
    elif meta and isinstance(meta.get("raw"), str):
        text = meta["raw"]
    if not text:
        return None

    # fenced json
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    # bare json object
    m = re.search(r"\{[^{}]*\"replacement_text\"[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass

    # last resort: treat entire non-empty prose as replacement if looks like novel text
    cleaned = text.strip()
    if cleaned and len(cleaned) > 40 and not cleaned.startswith("{"):
        return {"replacement_text": cleaned, "resolved_issue_ids": []}
    return None


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
    para_id = issue.get("paragraph_id") or issue.get("paragraph_key") or "p-0000"
    try:
        # support p-01-0001 and p-0000 styles
        parts = re.findall(r"\d+", str(para_id))
        if len(parts) >= 2:
            para_idx = max(int(parts[-1]) - 1, 0)
        elif parts:
            para_idx = int(parts[0])
        else:
            para_idx = 0
    except (IndexError, ValueError):
        para_idx = 0
    if para_idx >= len(paragraphs):
        para_idx = 0

    target = paragraphs[para_idx] if paragraphs else ""
    before = "\n\n".join(paragraphs[:para_idx])[-500:] if para_idx > 0 else ""
    after = "\n\n".join(paragraphs[para_idx + 1 :])[:500] if para_idx + 1 < len(paragraphs) else ""
    expected_hash = compute_hash(target)

    user_content = json.dumps(
        {
            "instruction": (
                "Return ONLY a JSON object with keys: replacement_text (string), "
                "resolved_issue_ids (array). No prose outside JSON."
            ),
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

    coerced = _coerce_patch_result(result, meta)
    if not coerced or not coerced.get("replacement_text"):
        logger.error(f"PatchEditor failed for issue {issue.get('issue_id')}: {meta}")
        return None

    return {
        "replacement_text": coerced.get("replacement_text", ""),
        "expected_hash": expected_hash,
        "paragraph_key": para_id,
        "scene_id": issue.get("scene_id"),
        "resolved_issue_ids": coerced.get("resolved_issue_ids", [issue.get("issue_id")]),
        "source_run_id": str(run.id) if run else None,
    }


class PatchStaleError(Exception):
    """B-10 / INV-08: expected_hash mismatch — zero mutation."""

    def __init__(self, paragraph_key: str | None = None, expected_hash: str | None = None):
        self.paragraph_key = paragraph_key
        self.expected_hash = expected_hash
        super().__init__(f"PATCH_STALE key={paragraph_key} expected={expected_hash}")


async def apply_patches(chapter_content: str, patches: list[dict]) -> str:
    """Apply patches with strict CAS (AI__.md v3.0 §11.3).

    Hash mismatch raises PatchStaleError and leaves content unchanged.
    No silent fallback to first non-empty paragraph.
    """
    if not patches:
        return chapter_content
    paragraphs = chapter_content.split("\n\n")
    for patch in patches:
        expected = patch.get("expected_hash")
        replacement = patch.get("replacement_text")
        if not expected or replacement is None:
            raise PatchStaleError(patch.get("paragraph_key") or patch.get("target_paragraph_key"), expected)
        applied = False
        for i, para in enumerate(paragraphs):
            if compute_hash(para) == expected:
                paragraphs[i] = replacement
                applied = True
                break
        if not applied:
            # B-10: never mutate unrelated paragraphs
            raise PatchStaleError(
                patch.get("paragraph_key") or patch.get("target_paragraph_key"),
                expected,
            )
    return "\n\n".join(paragraphs)
