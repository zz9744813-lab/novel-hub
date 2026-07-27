"""StateExtractorAgent - candidates only (AI__.md v3.0 §8.1 / B-06).

No writable Session. Does not write StoryEvent / L4 / L1.
"""
from __future__ import annotations

import json
import logging
import uuid

from app.agents.caller import call_agent

logger = logging.getLogger("novelforge.state_extractor")


async def extract_candidates(
    *,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    chapter_no: int,
    chapter_content: str,
    scenes: list[dict],
    outline_node,
    current_l4: dict,
) -> tuple[bool, list[dict], list[str]]:
    """LLM extract only. Returns (ok, events, errors).

    ok=False only on hard failures when content is substantial and outline
    expects state changes. Empty events with no expected changes is ok.
    """
    if hasattr(outline_node, "chapter_no"):
        outline_payload = {
            "chapter_no": outline_node.chapter_no,
            "goal": outline_node.goal,
            "expected_state_changes": outline_node.expected_state_changes,
        }
        involved = list(getattr(outline_node, "involved_character_ids", None) or [])
        expected = getattr(outline_node, "expected_state_changes", None) or []
    else:
        outline_payload = {
            "chapter_no": outline_node.get("chapter_no") if isinstance(outline_node, dict) else None,
            "goal": outline_node.get("goal") if isinstance(outline_node, dict) else None,
            "expected_state_changes": (
                outline_node.get("expected_state_changes") if isinstance(outline_node, dict) else None
            ),
        }
        involved = (
            list((outline_node or {}).get("involved_character_ids") or [])
            if isinstance(outline_node, dict)
            else []
        )
        expected = (
            (outline_node or {}).get("expected_state_changes") or []
            if isinstance(outline_node, dict)
            else []
        )

    # No entity cards and no expected changes → empty candidates (no LLM)
    if not current_l4 and not involved and not expected:
        logger.info(
            "StateExtractor skip LLM for chapter %s: no entities/L4/expected changes",
            chapter_no,
        )
        return True, [], []

    user_content = json.dumps(
        {
            "chapter_content": (chapter_content or "")[:6000],
            "scenes": [
                {
                    "scene_no": sc.get("scene_no"),
                    "summary": sc.get("summary"),
                    "content_excerpt": (sc.get("content") or "")[:800],
                    "paragraphs": [
                        {
                            "paragraph_key": p.get("paragraph_key") if isinstance(p, dict) else None,
                            "excerpt": (p.get("content") if isinstance(p, dict) else str(p))[:200],
                        }
                        for p in (sc.get("paragraphs") or [])[:20]
                    ],
                }
                for sc in (scenes or [])[:8]
            ],
            "current_l4": current_l4,
            "outline_node": outline_payload,
            "instruction": (
                "Return ONLY JSON object: {\"events\":[],\"conflicts\":[]}. "
                "Each event: event_key, entity_type, entity_id, field, old_value, "
                "new_value, certainty (must be explicit for commit), scene_no, "
                "evidence_paragraph_key, evidence_hash, evidence. "
                "No prose outside JSON."
            ),
        },
        ensure_ascii=False,
    )

    try:
        run, result, meta = await call_agent(
            book_id=book_id,
            agent_role="state_extractor",
            user_content=user_content,
            chapter_id=chapter_id,
        )
    except Exception as e:
        logger.error("StateExtractor call exception: %s", e)
        # Fail closed if outline expects changes or content is large
        if expected or (chapter_content and len(chapter_content) >= 800):
            return False, [], [f"extractor_exception:{e}"]
        return True, [], []

    if not result:
        # B-07: no soft-pass into finalize when we expected extractable content
        if expected:
            logger.error("StateExtractor empty but expected_state_changes present: %s", meta)
            return False, [], [
                str((meta or {}).get("block_reason") or (meta or {}).get("error") or "extraction empty")
            ]
        if chapter_content and len(chapter_content) >= 800 and (current_l4 or involved):
            # Allow empty candidates — finalize may still proceed without canon events
            logger.warning(
                "StateExtractor empty for chapter %s (no expected changes forced): %s",
                chapter_no,
                meta,
            )
            return True, [], []
        if not chapter_content or len(chapter_content) < 800:
            return False, [], [
                str((meta or {}).get("block_reason") or (meta or {}).get("error") or "extraction failed")
            ]
        return True, [], []

    events = result.get("events", []) if isinstance(result, dict) else []
    conflicts = result.get("conflicts", []) if isinstance(result, dict) else []
    if conflicts:
        logger.warning("StateExtractor found %s conflicts with L4", len(conflicts))

    # Normalize certainty: only explicit (or high) candidates kept for finalize
    normalized: list[dict] = []
    for i, e in enumerate(events or []):
        if not isinstance(e, dict):
            continue
        cert = e.get("certainty")
        if cert is None:
            e = {**e, "certainty": "explicit"}
            cert = "explicit"
        if cert not in ("explicit", "high"):
            continue
        if cert == "high":
            e = {**e, "certainty": "explicit"}
        if not e.get("event_key"):
            e = {**e, "event_key": f"evt-{i+1:02d}"}
        normalized.append(e)

    # Spec: expected_state_changes present but empty extract → cannot finalize
    if expected and not normalized:
        return False, [], ["expected_state_changes_but_empty_extract"]

    return True, normalized, [str(c) for c in (conflicts or [])]


# Back-compat shim — DO NOT write canon. Returns candidates only shape.
async def extract_and_commit(
    db,  # ignored — kept for signature compat
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    chapter_no: int,
    chapter_content: str,
    scenes: list[dict],
    outline_node,
    current_l4: dict,
    source_run_id: uuid.UUID,
) -> tuple[bool, list[str]]:
    """Deprecated path: no longer commits. Use extract_candidates + finalizer."""
    ok, events, errors = await extract_candidates(
        book_id=book_id,
        chapter_id=chapter_id,
        chapter_no=chapter_no,
        chapter_content=chapter_content,
        scenes=scenes,
        outline_node=outline_node,
        current_l4=current_l4,
    )
    # Stash candidates on a module-level for accidental callers — prefer explicit API
    extract_and_commit.last_candidates = events  # type: ignore[attr-defined]
    if not ok:
        return False, errors
    return True, errors
