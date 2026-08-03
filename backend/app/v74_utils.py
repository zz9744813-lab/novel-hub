"""v7.4 utilities: deterministic advisory lock, model binding service, context package assembler.

C-25: L4 lock uses deterministic SHA-256 -> bigint instead of unstable Python hash()
C-21: draft_writer model binding persisted to database
C-24: AI leak three-layer detection (Layer 0 hard block, Layer 1 regex prefilter, Layer 2 AILeakJudgeAgent)
C-35: Every Agent Attempt has Context Package persisted
"""
import uuid
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.models.tables import AgentModelBinding, ModelChangeLog, ModelRouteEvent, AgentContextPackage, AgentRun
from app.prompts import PROMPTS, AGENT_MODELS

logger = logging.getLogger("novelforge.v74")


# ═══════════════════════════════════════════════════════════════════════════════
# C-25: Deterministic advisory lock key
# ═══════════════════════════════════════════════════════════════════════════════

def advisory_lock_key(book_id: uuid.UUID) -> int:
    """§4: Deterministic SHA-256 -> PostgreSQL bigint for advisory lock.
    
    Guarantees:
    - Same UUID across processes produces same lock key
    - Same UUID across container restarts produces same lock key
    - 1M different UUIDs have negligible collision probability
    """
    digest = hashlib.sha256(book_id.bytes).digest()
    # Take first 8 bytes, interpret as unsigned 64-bit
    unsigned = int.from_bytes(digest[:8], byteorder="big", signed=False)
    # PostgreSQL advisory lock accepts signed 64-bit; keep positive range
    return unsigned & 0x7FFF_FFFF_FFFF_FFFF


async def acquire_book_lock(db: AsyncSession, book_id: uuid.UUID) -> None:
    """Acquire transaction-scoped advisory lock for a book.
    
    Lock is automatically released when transaction commits or rolls back.
    Must be called within an active transaction.
    """
    lock_key = advisory_lock_key(book_id)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})


# ═══════════════════════════════════════════════════════════════════════════════
# C-21/C-22: Model Binding Service
# ═══════════════════════════════════════════════════════════════════════════════

class ModelBindingService:
    """Manage agent model bindings with audit trail."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_binding(self, agent_role: str, book_id: uuid.UUID | None = None) -> AgentModelBinding | None:
        """Get effective model binding for an agent.
        
        Priority: book-level > global > None
        """
        # Try book-level first
        if book_id:
            result = await self.db.execute(
                select(AgentModelBinding).where(
                    AgentModelBinding.scope_type == "book",
                    AgentModelBinding.scope_id == book_id,
                    AgentModelBinding.agent_role == agent_role,
                )
            )
            binding = result.scalar_one_or_none()
            if binding:
                return binding
        
        # Fall back to global
        result = await self.db.execute(
            select(AgentModelBinding).where(
                AgentModelBinding.scope_type == "global",
                AgentModelBinding.scope_id.is_(None),
                AgentModelBinding.agent_role == agent_role,
            )
        )
        return result.scalar_one_or_none()
    
    async def get_or_create_binding(
        self,
        agent_role: str,
        provider: str,
        primary_model: str,
        fallback_model: str | None = None,
        reasoning_mode: str = "auto",
        book_id: uuid.UUID | None = None,
        updated_by: str = "system",
    ) -> AgentModelBinding:
        """Get existing binding or create new one with change log."""
        binding = await self.get_binding(agent_role, book_id)
        
        if binding:
            return binding
        
        # Create new binding
        binding_id = uuid.uuid4()
        binding = AgentModelBinding(
            id=binding_id,
            scope_type="book" if book_id else "global",
            scope_id=book_id,
            agent_role=agent_role,
            provider=provider,
            primary_model=primary_model,
            fallback_model=fallback_model,
            reasoning_mode=reasoning_mode,
            version=1,
            updated_by=updated_by,
        )
        self.db.add(binding)
        
        # Log the creation
        change_log = ModelChangeLog(
            id=uuid.uuid4(),
            binding_id=binding_id,
            agent_role=agent_role,
            old_provider=None,
            old_model=None,
            new_provider=provider,
            new_model=primary_model,
            old_reasoning_mode=None,
            new_reasoning_mode=reasoning_mode,
            reason="Initial binding creation",
            changed_by=updated_by,
        )
        self.db.add(change_log)
        
        await self.db.flush()
        return binding
    
    async def update_binding(
        self,
        binding_id: uuid.UUID,
        new_provider: str | None = None,
        new_model: str | None = None,
        new_reasoning_mode: str | None = None,
        new_fallback: str | None = None,
        reason: str = "Manual update",
        changed_by: str = "user",
    ) -> AgentModelBinding:
        """Update binding with mandatory change log entry."""
        result = await self.db.execute(
            select(AgentModelBinding).where(AgentModelBinding.id == binding_id)
        )
        binding = result.scalar_one()
        
        # Record old values
        old_provider = binding.provider
        old_model = binding.primary_model
        old_reasoning = binding.reasoning_mode
        
        # Update
        if new_provider:
            binding.provider = new_provider
        if new_model:
            binding.primary_model = new_model
        if new_reasoning_mode:
            binding.reasoning_mode = new_reasoning_mode
        if new_fallback is not None:
            binding.fallback_model = new_fallback
        binding.version += 1
        binding.updated_by = changed_by
        binding.updated_at = datetime.now(timezone.utc)
        
        # Log the change
        change_log = ModelChangeLog(
            id=uuid.uuid4(),
            binding_id=binding_id,
            agent_role=binding.agent_role,
            old_provider=old_provider,
            old_model=old_model,
            new_provider=binding.provider,
            new_model=binding.primary_model,
            old_reasoning_mode=old_reasoning,
            new_reasoning_mode=binding.reasoning_mode,
            reason=reason,
            changed_by=changed_by,
        )
        self.db.add(change_log)
        
        await self.db.flush()
        return binding


async def record_model_route(
    db: AsyncSession,
    run_id: uuid.UUID,
    attempt_no: int,
    agent_role: str,
    configured_provider: str,
    configured_model: str,
    actual_provider: str,
    actual_model: str,
    route_type: str,  # 'primary', 'retry', 'fallback'
    reason: str | None = None,
) -> ModelRouteEvent:
    """C-22: Record each model routing event (primary/retry/fallback)."""
    event = ModelRouteEvent(
        id=uuid.uuid4(),
        run_id=run_id,
        attempt_no=attempt_no,
        agent_role=agent_role,
        configured_provider=configured_provider,
        configured_model=configured_model,
        actual_provider=actual_provider,
        actual_model=actual_model,
        route_type=route_type,
        reason=reason,
    )
    db.add(event)
    await db.flush()
    return event


# ═══════════════════════════════════════════════════════════════════════════════
# C-24: AI Leak Three-Layer Detection
# ═══════════════════════════════════════════════════════════════════════════════

# Layer 0: Hard block patterns (protocol/structure errors)
LAYER0_BLOCK_PATTERNS = [
    # Unclosed thinking tags
    (r'<think>', r'</think>'),
    (r'<analysis>', r'</analysis>'),
    (r'<reasoning>', r'</reasoning>'),
    # Tool call leakage
    r'"tool_calls"\s*:',
    r'"function"\s*:',
    r'"arguments"\s*:',
    r'"usage"\s*:\s*\{',
    # JSON structure errors
    r'"unknown"\s*:\s*"[^"]{200,}"',  # long unknown fields
]

# Layer 1: Regex prefilter (candidates, not final decision)
LAYER1_REGEX_PATTERNS = [
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


def layer0_check(final_content: str, reasoning: str | None) -> tuple[bool, str]:
    """Layer 0: Hard block for protocol/structure errors.
    
    Returns (should_block, block_reason)
    """
    import re
    
    if not final_content:
        if reasoning:
            return True, "PROTOCOL_OR_STRUCTURE_LEAK: final empty with reasoning"
        return False, ""
    
    # Check for unclosed tags
    for open_pat, close_pat in [(r'<think>', r'</think>'), (r'<analysis>', r'</analysis>'), (r'<reasoning>', r'</reasoning>')]:
        open_count = len(re.findall(open_pat, final_content, re.IGNORECASE))
        close_count = len(re.findall(close_pat, final_content, re.IGNORECASE))
        if open_count != close_count:
            return True, f"PROTOCOL_OR_STRUCTURE_LEAK: unclosed {open_pat}"
    
    # Check for tool/usage leakage
    for pattern in [r'"tool_calls"', r'"function"\s*:', r'"usage"\s*:\s*\{']:
        if re.search(pattern, final_content):
            return True, f"PROTOCOL_OR_STRUCTURE_LEAK: tool/usage in final"
    
    return False, ""


def layer1_prefilter(final_content: str) -> list[dict]:
    """Layer 1: Regex prefilter - returns list of candidate spans.
    
    Does NOT make final decision - just marks suspicious paragraphs.
    """
    import re
    candidates = []
    
    paragraphs = final_content.split('\n\n')
    for i, para in enumerate(paragraphs):
        para_stripped = para.strip()
        if not para_stripped:
            continue
        
        for pattern in LAYER1_REGEX_PATTERNS:
            if re.search(pattern, para_stripped, re.IGNORECASE):
                # Check if it's inside quotes (dialogue context)
                # Simple heuristic: count quotes before and after
                quotes_before = para_stripped.count('"') + para_stripped.count('"') + para_stripped.count('"')
                if quotes_before % 2 == 1:  # Inside quotes
                    continue
                
                candidates.append({
                    "paragraph_id": f"p-{i:04d}",
                    "pattern": pattern,
                    "span": para_stripped[:100],
                })
                break
    
    return candidates


async def call_aileak_judge(
    target_paragraph: str,
    prev_paragraph: str | None,
    next_paragraph: str | None,
    agent_role: str,
    prefilter_hits: list[dict],
    model_gateway,  # Injected dependency
) -> dict:
    """Layer 2: AILeakJudgeAgent - semantic judgment on suspicious content.
    
    C-24: Does NOT read reasoning or raw response.
    Only reads target + context + role + prefilter hits.
    """
    from app.prompts import PROMPTS
    
    system_prompt = """你是 AI 元评论泄漏判定 Agent。

任务：判断目标段落中的可疑表达，是小说叙事、角色对白、作者式旁白，
还是模型分析、规划、自检、格式说明或生成过程元评论。

规则：
1. 只做分类，不重写正文。
2. 不得因成人、暴力、露骨程度或道德偏好判定泄漏。
3. 引号内角色对白必须结合上下文判断。
4. "以下是正文""我将开始""字数统计""这一段应该怎么写"等通常属于元评论。
5. confidence < 0.85 时返回 uncertain。
6. 输出只能是 JSON。"""

    user_content = f"""目标段落：
{target_paragraph}

前一段（如有）：
{prev_paragraph or '（无）'}

后一段（如有）：
{next_paragraph or '（无）'}

Agent Role: {agent_role}
预筛命中项：{prefilter_hits}

请输出 JSON 判断结果。"""

    # Call model with JSON schema enforcement
    result = await model_gateway.stream_with_retry(
        system_prompt=system_prompt,
        user_content=user_content,
        model="deepseek-v4-flash",  # Fixed model for judge
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    
    # Parse JSON response
    import json
    try:
        judgment = json.loads(result.final_content) if result.final_content else {}
        return {
            "classification": judgment.get("classification", "uncertain"),
            "confidence": judgment.get("confidence", 0.0),
            "evidence_span": judgment.get("evidence_span", ""),
            "decision": judgment.get("decision", "allow"),
            "reason": judgment.get("reason", ""),
            "safe_to_remove_directly": judgment.get("safe_to_remove_directly", False),
        }
    except json.JSONDecodeError:
        return {
            "classification": "uncertain",
            "confidence": 0.0,
            "decision": "human_review",
            "reason": "Failed to parse judge output",
        }


# ═══════════════════════════════════════════════════════════════════════════════
# C-35: Context Package Assembly
# ═══════════════════════════════════════════════════════════════════════════════

def compute_template_hash(system_prompt: str) -> str:
    """Compute deterministic hash of prompt template."""
    return hashlib.sha256(system_prompt.encode()).hexdigest()[:16]


def compute_rendered_hash(rendered_prompt: str) -> str:
    """Compute deterministic hash of fully rendered prompt."""
    return hashlib.sha256(rendered_prompt.encode()).hexdigest()


async def save_context_package(
    db: AsyncSession,
    run_id: uuid.UUID,
    attempt_no: int,
    book_id: uuid.UUID,
    agent_role: str,
    provider: str,
    model: str,
    prompt_version: str,
    system_prompt: str,
    rendered_prompt: str,
    request_params: dict,
    assembly_manifest: dict,
    l4_refs: list,
    l1_refs: list,
    l2_refs: list,
    l3_refs: list,
    genre_profile_id: uuid.UUID | None = None,
    story_evidence_refs: list | None = None,
    external_evidence_refs: list | None = None,
    prompt_snapshot: dict | None = None,
    chapter_id: uuid.UUID | None = None,
    scene_id: uuid.UUID | None = None,
    assembler_version: str = "1.0",
    context_schema_version: str = "1.0",
) -> AgentContextPackage:
    """C-35: Persist context package for each Agent attempt."""
    
    # Compute hashes
    template_hash = compute_template_hash(system_prompt)
    rendered_hash = compute_rendered_hash(rendered_prompt)
    
    # Estimate tokens for Chinese-heavy prompts (v2.0 COST-001).
    # Measured ratio actual/naive(//4) was 1.60–2.87x; use role-aware safe estimate.
    from app.token_estimate import safe_token_estimate

    token_estimate = safe_token_estimate(rendered_prompt, agent_role=agent_role)
    
    pkg = AgentContextPackage(
        id=uuid.uuid4(),
        run_id=run_id,
        attempt_no=attempt_no,
        book_id=book_id,
        chapter_id=chapter_id,
        scene_id=scene_id,
        agent_role=agent_role,
        provider=provider,
        model=model,
        prompt_version=prompt_version,
        prompt_template_hash=template_hash,
        context_schema_version=context_schema_version,
        assembler_version=assembler_version,
        request_parameters=request_params,
        assembly_manifest=assembly_manifest,
        prompt_snapshot=prompt_snapshot or {},
        l4_entity_refs=l4_refs,
        l1_ledger_refs=l1_refs,
        l2_summary_refs=l2_refs,
        l3_summary_refs=l3_refs,
        genre_profile_ref=genre_profile_id,
        story_evidence_refs=story_evidence_refs or [],
        external_evidence_refs=external_evidence_refs or [],
        assembled_token_estimate=token_estimate,
        rendered_prompt_hash=rendered_hash,
        publish_state="pending",
    )
    db.add(pkg)
    await db.flush()
    return pkg