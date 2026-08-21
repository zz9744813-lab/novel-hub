"""StateExtractorAgent - candidates only (AI__.md v3.0 §8.1 / B-06 + v9 CCNE §29).

v9: facts and attribution are separated.
- reaction_evidence: observable reactions only (who did what, where).
- attributions: constrained selection from provided Core/Belief/Goal IDs;
  anything without support must be "unresolved" (never invented psychology).

No writable Session. Does not write StoryEvent / L4 / L1.
"""
from __future__ import annotations

import json
import logging
import uuid

from app.agents.caller import call_agent
from app.contracts.narrative import ReactionAttribution, ReactionEvidence

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
    scene_contracts: list[dict] | None = None,
    core_anchors: list[dict] | None = None,
) -> tuple[bool, list[dict], list[str], dict]:
    """LLM extract only. Returns (ok, events, errors, extras).

    extras: {"reaction_evidence": [...], "attributions": [...]} (v9).
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

    extras: dict = {"reaction_evidence": [], "attributions": []}

    # No entity cards and no expected changes → empty candidates (no LLM)
    if not current_l4 and not involved and not expected:
        logger.info(
            "StateExtractor skip LLM for chapter %s: no entities/L4/expected changes",
            chapter_no,
        )
        return True, [], [], extras

    # v9: legal ID pools for constrained attribution
    legal_anchor_ids = [
        str(a.get("anchor_code") or a.get("id"))
        for a in (core_anchors or [])
        if isinstance(a, dict)
    ]
    legal_belief_keys: list[str] = []
    for _cid, payload in (current_l4 or {}).items():
        state = payload.get("state") if isinstance(payload, dict) else payload
        if isinstance(state, dict) and isinstance(state.get("beliefs"), dict):
            legal_belief_keys.extend(list(state["beliefs"].keys())[:12])

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
            "scene_contracts": (scene_contracts or [])[:8],
            "legal_attribution_ids": {
                "core_anchor_ids": legal_anchor_ids[:40],
                "belief_keys": legal_belief_keys[:60],
            },
            "instruction": (
                "Return ONLY JSON object: {\"events\":[],\"conflicts\":[],"
                "\"reaction_evidence\":[],\"attributions\":[]}. "
                "Each event: event_key, entity_type, entity_id, field, old_value, "
                "new_value, certainty (must be explicit for commit), scene_no, "
                "evidence_paragraph_key, evidence_hash, evidence. "
                "reaction_evidence: {reaction_key, character_id, scene_no, "
                "evidence_paragraph_key, reaction_summary, weight}. "
                "attributions: {reaction_key, cause_event_keys, core_anchor_ids, "
                "belief_keys, goal_keys, status, reason} — IDs may ONLY come from "
                "legal_attribution_ids; unsupported must be status=\"unresolved\". "
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
            return False, [], [f"extractor_exception:{e}"], extras
        return True, [], [], extras

    if not result:
        # B-07: no soft-pass into finalize when we expected extractable content
        if expected:
            logger.error("StateExtractor empty but expected_state_changes present: %s", meta)
            return False, [], [
                str((meta or {}).get("block_reason") or (meta or {}).get("error") or "extraction empty")
            ], extras
        if chapter_content and len(chapter_content) >= 800 and (current_l4 or involved):
            # Allow empty candidates — finalize may still proceed without canon events
            logger.warning(
                "StateExtractor empty for chapter %s (no expected changes forced): %s",
                chapter_no,
                meta,
            )
            return True, [], [], extras
        if not chapter_content or len(chapter_content) < 800:
            return False, [], [
                str((meta or {}).get("block_reason") or (meta or {}).get("error") or "extraction failed")
            ], extras
        return True, [], [], extras

    events = result.get("events", []) if isinstance(result, dict) else []
    conflicts = result.get("conflicts", []) if isinstance(result, dict) else []
    if conflicts:
        logger.warning("StateExtractor found %s conflicts with L4", len(conflicts))

    # v9: normalize reaction evidence + constrained attributions
    reaction_evidence: list[dict] = []
    for r in (result.get("reaction_evidence") or []) if isinstance(result, dict) else []:
        try:
            ev = ReactionEvidence.model_validate(r)
            reaction_evidence.append(ev.model_dump(mode="json"))
        except Exception:
            continue
    legal_anchor_set = set(legal_anchor_ids)
    legal_belief_set = set(legal_belief_keys)
    attributions: list[dict] = []
    for a in (result.get("attributions") or []) if isinstance(result, dict) else []:
        try:
            at = ReactionAttribution.model_validate(a)
        except Exception:
            continue
        # Constrained: drop any anchor/belief not in the legal pools
        at.core_anchor_ids = [i for i in at.core_anchor_ids if i in legal_anchor_set]
        at.belief_keys = [k for k in at.belief_keys if k in legal_belief_set]
        if not at.has_any_support() and at.status == "supported":
            at.status = "unresolved"
            at.reason = at.reason or "自动降级：引用的 ID 不在合法池内"
        attributions.append(at.model_dump(mode="json"))
    extras = {"reaction_evidence": reaction_evidence, "attributions": attributions}

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
        return False, [], ["expected_state_changes_but_empty_extract"], extras

    return True, normalized, [str(c) for c in (conflicts or [])], extras


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
    ok, events, errors, _extras = await extract_candidates(
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
