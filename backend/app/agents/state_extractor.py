"""StateExtractorAgent - candidates only (AI__.md v3.0 §8.1 / B-06 + v9 CCNE §29).

v9: facts and attribution are separated.
- reaction_evidence: observable reactions only (who did what, where).
- attributions: constrained selection from provided Core/Belief/Goal IDs;
  anything without support must be "unresolved" (never invented psychology).

No writable Session. Does not write StoryEvent / L4 / L1.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid

from app.agents.caller import call_agent
from app.contracts.narrative import ReactionAttribution, ReactionEvidence
from app.engine.event_evidence import validate_explicit_event_evidence

logger = logging.getLogger("novelforge.state_extractor")


def _normalize_commit_eligible_events(
    events: list | None,
    legal_provisional_event_keys: set[str],
    paragraphs_by_key: dict[str, dict],
) -> list[dict]:
    """Return only events that can actually enter hard canon.

    Early-stop decisions and the final extraction result must use the same
    validation path.  In particular, an event that merely says
    ``certainty=explicit`` but fails ``ExtractedStoryEvent`` validation must
    not suppress the bounded repair call.
    """
    from app.contracts.narrative import ExtractedStoryEvent

    normalized: list[dict] = []
    for i, event in enumerate(events or []):
        if not isinstance(event, dict):
            continue
        if not event.get("event_key"):
            event = {**event, "event_key": f"evt-{i + 1:02d}"}
        try:
            event_model = ExtractedStoryEvent.model_validate(event)
        except Exception:
            logger.warning("drop malformed extracted event: %s", str(event)[:120])
            continue
        item = event_model.model_dump(mode="json")
        if item.get("certainty") is None:
            item["certainty"] = "unknown"
        if item["certainty"] != "explicit":
            continue
        evidence_error = validate_explicit_event_evidence(
            item,
            paragraphs_by_key,
        )
        if evidence_error:
            logger.warning(
                "drop ungrounded extracted event %s: %s",
                item.get("event_key"),
                evidence_error,
            )
            continue
        # v9.1 §9.1: invalid provisional key references are stripped, not trusted
        provisional_key = item.get("realized_provisional_event_key")
        if provisional_key is not None and str(provisional_key) not in legal_provisional_event_keys:
            logger.warning(
                "strip unknown realized_provisional_event_key %r from %s",
                provisional_key,
                item.get("event_key"),
            )
            item["realized_provisional_event_key"] = None
        normalized.append(item)
    return normalized


def _body_paragraph_index(
    scenes: list[dict],
    chapter_content: str,
) -> dict[str, dict]:
    """Build the exact paragraph evidence index exposed to the model.

    Pipeline callers supply final-artifact paragraph keys.  The fallback body
    chunks keep excerpt grounding available for older callers that only pass
    chapter text; their synthetic keys are not advertised as canonical keys.
    """
    paragraphs: dict[str, dict] = {}
    body_chunks: list[str] = []
    for scene in scenes or []:
        if not isinstance(scene, dict):
            continue
        scene_paragraphs = scene.get("paragraphs") or []
        for paragraph in scene_paragraphs:
            if not isinstance(paragraph, dict):
                continue
            key = str(paragraph.get("paragraph_key") or "").strip()
            content = str(paragraph.get("content") or "")
            if content.strip():
                body_chunks.append(content)
            if not key or not content.strip():
                continue
            paragraphs[key] = {
                "content": content,
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        if not scene_paragraphs:
            scene_content = str(scene.get("content") or "")
            body_chunks.extend(
                part for part in scene_content.split("\n\n") if part.strip()
            )

    if not body_chunks:
        body_chunks.extend(
            part for part in (chapter_content or "").split("\n\n") if part.strip()
        )
    for index, content in enumerate(body_chunks, start=1):
        paragraphs.setdefault(
            f"__body__{index:04d}",
            {
                "content": content,
                "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
        )
    return paragraphs


def _prompt_paragraph(paragraph: object) -> dict:
    content = (
        str(paragraph.get("content") or "")
        if isinstance(paragraph, dict)
        else str(paragraph)
    )
    return {
        "paragraph_key": (
            paragraph.get("paragraph_key")
            if isinstance(paragraph, dict)
            else None
        ),
        "excerpt": content[:200],
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


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

    # v9: legal ID pools for constrained attribution (spec §12)
    legal_anchor_ids = [
        str(a.get("anchor_code") or a.get("id"))
        for a in (core_anchors or [])
        if isinstance(a, dict)
    ]
    legal_belief_keys: list[str] = []
    legal_goal_keys: list[str] = []
    legal_relationship_refs: list[str] = []
    for _cid, payload in (current_l4 or {}).items():
        state = payload.get("state") if isinstance(payload, dict) else payload
        if isinstance(state, dict):
            if isinstance(state.get("beliefs"), dict):
                legal_belief_keys.extend(list(state["beliefs"].keys())[:12])
            if isinstance(state.get("goals"), dict):
                legal_goal_keys.extend(list(state["goals"].keys())[:8])
            if isinstance(state.get("relationships"), dict):
                legal_relationship_refs.extend(list(state["relationships"].keys())[:8])

    # v9.1 §9.1: provisional event keys are legal attribution causes AND the
    # only legal values for realized_provisional_event_key
    provisional_event_keys: list[str] = []
    for c in (scene_contracts or [])[:8]:
        if isinstance(c, dict):
            for ev in c.get("provisional_events") or []:
                if isinstance(ev, dict) and ev.get("event_key"):
                    provisional_event_keys.append(str(ev["event_key"]))

    evidence_paragraphs = _body_paragraph_index(scenes, chapter_content)

    user_content = json.dumps(
        {
            "chapter_content": (chapter_content or "")[:6000],
            "scenes": [
                {
                    "scene_no": sc.get("scene_no"),
                    "summary": sc.get("summary"),
                    "content_excerpt": (sc.get("content") or "")[:800],
                    "paragraphs": [
                        _prompt_paragraph(p)
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
                "goal_keys": legal_goal_keys[:40],
                "cause_event_keys": provisional_event_keys[:80],
                "relationship_refs": legal_relationship_refs[:40],
            },
            "instruction": (
                "Return ONLY JSON object: {\"events\":[],\"conflicts\":[],"
                "\"reaction_evidence\":[],\"attributions\":[]}. "
                "When outline_node.expected_state_changes is non-empty, inspect "
                "the正文 and emit at least one evidence-backed event for every "
                "change that is explicitly realized; never treat the outline "
                "alone as evidence and never invent an event. "
                "Each event: event_key, entity_type, entity_id, field, old_value, "
                "new_value, certainty (must be explicit for commit), scene_no, "
                "evidence_paragraph_key, evidence_hash, evidence, "
                "realized_provisional_event_key — when the event realizes one of "
                "the provisional_events in scene_contracts, set it to that event's "
                "event_key (e.g. P-001-02-03); otherwise null. "
                "reaction_evidence: {reaction_key, character_id, scene_no, "
                "evidence_paragraph_key, reaction_summary, weight}. "
                "attributions: {reaction_key, cause_event_keys, core_anchor_ids, "
                "belief_keys, goal_keys, relationship_refs, status, reason} — IDs "
                "may ONLY come from legal_attribution_ids; unsupported must be "
                "status=\"unresolved\". No prose outside JSON."
            ),
        },
        ensure_ascii=False,
    )

    # A valid empty JSON object is not proof that the chapter has no state
    # changes: structured gateways occasionally return an empty candidate list
    # after spending the response budget on reasoning, or omit explicit
    # certainty on the first pass.  Give expected-change chapters one bounded
    # repair request.  The retry still has to cite observable正文 evidence;
    # it never promotes outline plans into canon.
    call_results: list[tuple[object | None, dict]] = []
    max_calls = 2 if expected else 1
    retry_instruction = (
        "\n\n[EMPTY_EXTRACT_RETRY]\n"
        "上一轮没有提交任何可入正史的 explicit event。请重新逐段核对正文："
        "对每一项 expected_state_changes，只在正文明确发生或确认且能给出"
        "evidence_paragraph_key/evidence_hash/evidence 时输出至少一个事件；"
        "certainty 必须为 explicit。若正文确实没有可观察证据，仍返回空数组，"
        "不得根据大纲或推测补写事实。只能返回原 JSON 对象。"
    )
    for call_no in range(max_calls):
        prompt = user_content if call_no == 0 else user_content + retry_instruction
        try:
            _run, candidate, candidate_meta = await call_agent(
                book_id=book_id,
                agent_role="state_extractor",
                user_content=prompt,
                chapter_id=chapter_id,
            )
            candidate_meta = dict(candidate_meta or {})
        except Exception as e:
            logger.error("StateExtractor call exception (attempt %s): %s", call_no + 1, e)
            candidate = None
            candidate_meta = {"error": f"extractor_exception:{e}"}

        call_results.append((candidate, candidate_meta))

        # Stop early only when the model supplied at least one event that the
        # final canon normalizer can commit. Malformed/subjective/inferred-only
        # responses get the one repair pass as well.
        has_commit_eligible_event = bool(
            isinstance(candidate, dict)
            and _normalize_commit_eligible_events(
                candidate.get("events"),
                set(provisional_event_keys),
                evidence_paragraphs,
            )
        )
        if has_commit_eligible_event:
            break

    # Prefer the first response that contains a commit-eligible candidate; otherwise
    # keep the final response so its gateway metadata remains diagnostic.
    selected_index = next(
        (
            index
            for index, (candidate, _meta) in enumerate(call_results)
            if isinstance(candidate, dict)
            and _normalize_commit_eligible_events(
                candidate.get("events"),
                set(provisional_event_keys),
                evidence_paragraphs,
            )
        ),
        None,
    )
    if selected_index is None:
        # Preserve a valid-but-empty payload over a later transport failure so
        # the final diagnostic remains about extraction, not just the retry.
        selected_index = next(
            (
                index
                for index in range(len(call_results) - 1, -1, -1)
                if call_results[index][0] is not None
            ),
            len(call_results) - 1,
        )
    result, meta = call_results[selected_index]
    meta = {
        **(meta or {}),
        "empty_extract_retry_attempts": len(call_results),
    }

    if not result:
        # Fail closed if outline expects changes or content is large
        if expected or (chapter_content and len(chapter_content) >= 800):
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

    # v9.1 §13 certainty fail-closed: only "explicit" enters hard canon.
    # Early-stop and final commit share this exact normalization path.
    normalized = _normalize_commit_eligible_events(
        events,
        set(provisional_event_keys),
        evidence_paragraphs,
    )

    # Spec: expected_state_changes present but empty extract → cannot finalize
    if expected and not normalized:
        return False, [], ["expected_state_changes_but_empty_extract"], extras

    # v9: normalize reaction evidence + constrained attributions (spec §12)
    reaction_evidence: list[dict] = []
    for r in (result.get("reaction_evidence") or []) if isinstance(result, dict) else []:
        try:
            ev = ReactionEvidence.model_validate(r)
            reaction_evidence.append(ev.model_dump(mode="json"))
        except Exception:
            continue

    legal_anchor_set = set(legal_anchor_ids)
    legal_belief_set = set(legal_belief_keys)
    legal_goal_set = set(legal_goal_keys)
    legal_rel_set = set(legal_relationship_refs)
    # attribution causes may cite this chapter's provisional events OR the
    # actual event keys produced by this very extraction
    legal_prov_keys = set(provisional_event_keys)
    legal_cause_set = legal_prov_keys | {
        str(e.get("event_key")) for e in normalized if e.get("event_key")
    }

    attributions: list[dict] = []
    for a in (result.get("attributions") or []) if isinstance(result, dict) else []:
        try:
            at = ReactionAttribution.model_validate(a)
        except Exception:
            continue
        # Constrained: drop any ID not in its legal pool (all five lists)
        at.core_anchor_ids = [i for i in at.core_anchor_ids if i in legal_anchor_set]
        at.belief_keys = [k for k in at.belief_keys if k in legal_belief_set]
        at.goal_keys = [k for k in at.goal_keys if k in legal_goal_set]
        at.relationship_refs = [r for r in at.relationship_refs if r in legal_rel_set]
        at.cause_event_keys = [k for k in at.cause_event_keys if k in legal_cause_set]
        if not at.has_any_support() and at.status == "supported":
            at.status = "unresolved"
            at.reason = at.reason or "自动降级：引用的 ID 不在合法池内"
        attributions.append(at.model_dump(mode="json"))
    extras = {"reaction_evidence": reaction_evidence, "attributions": attributions}

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
