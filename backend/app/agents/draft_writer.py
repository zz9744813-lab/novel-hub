"""DraftWriterAgent - writes one scene at a time, streaming.
Per §7.2 Step 5 + §A.3 v7.3 + v9 CCNE §25.

The scene_contract is a behavioral/state boundary, not a prose manual:
DraftWriter may freely choose literary execution but must not violate
hard causal effects, knowledge boundaries, belief deltas, key exit_state.

P0-03: no DB session during LLM.
"""
from __future__ import annotations

import uuid
import json
import logging
from app.agents.caller import call_agent
from app.contracts.narrative import SceneContract

logger = logging.getLogger("novelforge.draft_writer")


def _contract_for_prompt(scene_contract) -> dict | None:
    if scene_contract is None:
        return None
    if isinstance(scene_contract, SceneContract):
        return scene_contract.model_dump(mode="json", by_alias=True, exclude={"contract_hash"})
    if isinstance(scene_contract, dict):
        try:
            c = SceneContract.model_validate(scene_contract)
            return c.model_dump(mode="json", by_alias=True, exclude={"contract_hash"})
        except Exception:
            return scene_contract
    return None


async def write_scene(
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    scene_plan: dict,
    context_package: dict,
    previous_scene_tail: str = "",
    target_word_count: int = 2000,
    scene_contract: dict | SceneContract | None = None,
    scene_style_contract: dict | None = None,
    **_deprecated,
) -> tuple[str | None, str | None]:
    """Write a single scene. Returns (content, error_reason)."""
    contract_payload = _contract_for_prompt(scene_contract)
    if contract_payload is None and isinstance(context_package, dict):
        contract_payload = context_package.get("scene_contract")

    user_content = json.dumps(
        {
            "scene_plan": scene_plan,
            "scene_contract": contract_payload,
            "scene_style_contract": scene_style_contract,  # spec §47
            "context_package": context_package,
            "previous_scene_tail": previous_scene_tail,
            "target_word_count": target_word_count,
        },
        ensure_ascii=False,
    )

    run, result, meta = await call_agent(
        book_id=book_id,
        agent_role="draft_writer",
        user_content=user_content,
        chapter_id=chapter_id,
        scene_id=None,
    )

    if result is None:
        return None, meta.get("block_reason", "unknown")

    if isinstance(result, str) and result.startswith("PIPELINE_BLOCKED"):
        return None, result

    return result, None
