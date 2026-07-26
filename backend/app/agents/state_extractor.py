"""StateExtractorAgent + StateCommitter - extracts events and commits L4 atomically.
Per §7.2 Step 9-10 + §5.5 v7.3.

P0-03: LLM phase without holding caller's session across await.
Early chapters without entity cards skip LLM and soft-pass.
"""
from __future__ import annotations

import uuid
import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from app.agents.caller import call_agent
from app.engine.memory import commit_l4_with_events

logger = logging.getLogger("novelforge.state_extractor")


async def extract_and_commit(
    db: AsyncSession,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    chapter_no: int,
    chapter_content: str,
    scenes: list[dict],
    outline_node,
    current_l4: dict,
    source_run_id: uuid.UUID,
) -> tuple[bool, list[str]]:
    """Extract state events then commit in the *write* session.

    LLM call is session-free (call_agent). When there are no L4 entities /
    model only emits reasoning, soft-pass so finalizer can still commit.
    """
    if hasattr(outline_node, "chapter_no"):
        outline_payload = {
            "chapter_no": outline_node.chapter_no,
            "goal": outline_node.goal,
            "expected_state_changes": outline_node.expected_state_changes,
        }
        involved = list(getattr(outline_node, "involved_character_ids", None) or [])
    else:
        outline_payload = {
            "chapter_no": outline_node.get("chapter_no") if isinstance(outline_node, dict) else None,
            "goal": outline_node.get("goal") if isinstance(outline_node, dict) else None,
            "expected_state_changes": (
                outline_node.get("expected_state_changes") if isinstance(outline_node, dict) else None
            ),
        }
        involved = list((outline_node or {}).get("involved_character_ids") or []) if isinstance(outline_node, dict) else []

    # No entity cards yet → skip expensive LLM (often REASONING_ONLY on small models)
    if not current_l4 and not involved:
        logger.warning(
            f"StateExtractor skip LLM for chapter {chapter_no}: no entities/L4; soft-pass"
        )
        try:
            await commit_l4_with_events(
                db=db,
                book_id=book_id,
                chapter_id=chapter_id,
                as_of_chapter=chapter_no,
                events=[],
                source_run_id=source_run_id or uuid.uuid4(),
            )
        except Exception as e:
            logger.warning(f"commit_l4 empty soft-fail: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
        try:
            await db.commit()
        except Exception:
            try:
                await db.rollback()
            except Exception:
                pass
        return True, []

    user_content = json.dumps(
        {
            "chapter_content": (chapter_content or "")[:6000],
            "scenes": [
                {
                    "scene_no": sc.get("scene_no"),
                    "summary": sc.get("summary"),
                    "content_excerpt": (sc.get("content") or "")[:800],
                }
                for sc in (scenes or [])[:5]
            ],
            "paragraphs": [],
            "current_l4": current_l4,
            "outline_node": outline_payload,
            "instruction": (
                "Return ONLY JSON object: {\"events\":[],\"conflicts\":[]}. "
                "No prose, no reasoning outside JSON."
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
        logger.warning(f"StateExtractor call exception soft-pass: {e}")
        run, result, meta = None, None, {"error": str(e)}

    if not result:
        if chapter_content and len(chapter_content) >= 800:
            logger.warning(
                f"StateExtractor failed soft-pass for chapter {chapter_no}: {meta}"
            )
            events, conflicts = [], []
        else:
            logger.error(f"StateExtractor failed: {meta}")
            return False, [str((meta or {}).get("block_reason") or (meta or {}).get("error") or "extraction failed")]
    else:
        events = result.get("events", []) if isinstance(result, dict) else []
        conflicts = result.get("conflicts", []) if isinstance(result, dict) else []

    if conflicts:
        logger.warning(f"StateExtractor found {len(conflicts)} conflicts with L4")

    explicit_events = [
        e for e in (events or [])
        if isinstance(e, dict) and e.get("certainty") in (None, "explicit", "high", "medium")
    ]

    try:
        await commit_l4_with_events(
            db=db,
            book_id=book_id,
            chapter_id=chapter_id,
            as_of_chapter=chapter_no,
            events=explicit_events,
            source_run_id=source_run_id or (run.id if run else uuid.uuid4()),
        )
    except Exception as e:
        logger.warning(f"commit_l4_with_events soft-fail: {e}")
        try:
            await db.rollback()
        except Exception:
            pass

    try:
        await db.commit()
    except Exception as e:
        logger.warning(f"state extractor commit soft-fail: {e}")
        try:
            await db.rollback()
        except Exception:
            pass
    return True, []
