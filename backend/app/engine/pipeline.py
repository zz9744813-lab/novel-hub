"""Full chapter pipeline - 13 steps per §7.2 v7.3.

§2.5 FIX: Each phase uses its own short-lived session.
LLM calls happen BETWEEN sessions — no DB connection held during API waits.
"""
import uuid
import json
import logging
from sqlalchemy import select
from app.database import async_session_factory
from app.state_machine import ChapterState
from app.models import (
    BookSetting, Chapter, ChapterVersion, OutlineNode, OutlineVersion, OutlineVolume,
    MemoryL4StateSnapshot, QueryPlan, RetrievalRun,
)
from app.engine.chapter_target import (
    chapter_length_issues,
    distribute_scene_targets,
    parse_chapter_target_chars,
)
from app.engine.outline import check_required_dependencies
from app.engine.retrieval import (
    dependency_resolver, state_resolver, plot_thread_resolver,
    event_ledger_search, full_text_search, candidate_merge_and_score,
    evidence_ranker_agent, query_planner_agent, deterministic_query_template,
    build_retrieval_candidates, causal_frontier_step,
)
from app.engine.context_assembler import assemble_context
from app.engine.relevant_state import select_relevant_scene_state
from app.agents.chapter_planner import plan_chapter
from app.agents.draft_writer import write_scene
from app.agents.review_agent import review_chapter
from app.agents.patch_editor import generate_patch, apply_patches, PatchStaleError
from app.agents.state_extractor import extract_candidates
from app.agents.drift_audit import run_drift_audit
from app.engine.causal_compile import compile_chapter_contracts
from app.engine.memory_compiler import generate_l2, generate_l3, volume_stage_window
from app.engine.outcomes import PipelineOutcome, PipelineResult
from app.engine.step_runner import (
    RunContext, run_step, content_hash,
    ControlRequestedError, LeaseLostError, RetryableStepError, PermanentStepError,
    PIPELINE_VERSION,
)
import os
import socket

logger = logging.getLogger("novelforge.pipeline")


async def _set_chapter_status(
    chapter_id: uuid.UUID,
    status: str,
    reason: str | None = None,
    chapter_run_id: uuid.UUID | None = None,
):
    """P1 CORE-001: status writes go through State Transition Service."""
    from app.engine.state_transition import transition_chapter

    await transition_chapter(
        chapter_id,
        status,
        reason=reason or f"pipeline->{status}",
        actor="pipeline",
        run_id=chapter_run_id,
    )
    try:
        from app.events import publish_event
        await publish_event(
            "chapter.updated",
            {
                "chapter_id": str(chapter_id),
                "chapter_run_id": str(chapter_run_id) if chapter_run_id else None,
                "status": status,
                "reason": reason,
            },
        )
    except Exception:
        logger.debug("chapter status event publish failed", exc_info=True)


async def _get_outline_node(outline_node_id: uuid.UUID) -> OutlineNode | None:
    """Fetch outline node in a short-lived session (detached after close)."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(OutlineNode).where(OutlineNode.id == outline_node_id)
        )
        return result.scalar_one_or_none()


async def execute_pipeline(
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    chapter_no: int,
    chapter_run_id: uuid.UUID | None = None,
) -> PipelineResult:
    """Execute the full chapter pipeline and return a typed outcome (B-01).

    §2.5: Uses short-lived sessions for each phase.
    LLM calls happen outside session context. Never bare-return on failure.
    """
    def _result(outcome: PipelineOutcome, **kw) -> PipelineResult:
        return PipelineResult(
            outcome=outcome,
            chapter_id=chapter_id,
            chapter_run_id=chapter_run_id,
            **kw,
        )

    worker_id = os.environ.get("WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}"
    ctx = RunContext(
        book_id=book_id,
        chapter_id=chapter_id,
        chapter_no=chapter_no,
        run_id=chapter_run_id,
        worker_id=worker_id,
        pipeline_version=PIPELINE_VERSION,
    )
    chapter_target = None
    chapter_target_error = None

    # === Phase 1: Setup + DependencyGate ===
    async with async_session_factory() as db:
        chapter = (await db.execute(
            select(Chapter).where(Chapter.id == chapter_id)
        )).scalar_one_or_none()
        if not chapter:
            logger.error(f"Chapter {chapter_id} not found")
            return _result(PipelineOutcome.PERMANENT_FAILURE, error_code="chapter_not_found")

        outline_node = (await db.execute(
            select(OutlineNode).where(OutlineNode.id == chapter.outline_node_id)
        )).scalar_one_or_none()
        if not outline_node:
            logger.error(f"Outline node not found for chapter {chapter_no}")
            await _set_chapter_status(chapter_id, ChapterState.FAILED.value, "outline node missing")
            return _result(PipelineOutcome.PERMANENT_FAILURE, error_code="outline_missing")

        outline_node_id = outline_node.id
        outline_version_id = outline_node.outline_version_id

        target_setting = (
            await db.execute(
                select(BookSetting.value).where(
                    BookSetting.book_id == book_id,
                    BookSetting.key == "chapter_target_chars",
                )
            )
        ).scalar_one_or_none()
        try:
            chapter_target = parse_chapter_target_chars(target_setting)
        except ValueError as exc:
            chapter_target_error = str(exc)

        await db.commit()

    if chapter_target is None:
        logger.error("invalid chapter length contract for book %s: %s", book_id, chapter_target_error)
        await _set_chapter_status(
            chapter_id,
            ChapterState.FAILED.value,
            "invalid_chapter_length_contract",
            chapter_run_id,
        )
        return _result(
            PipelineOutcome.PERMANENT_FAILURE,
            error_code="invalid_chapter_length_contract",
            detail={"error": chapter_target_error},
        )

    await _set_chapter_status(chapter_id, ChapterState.DEPENDENCY_CHECK.value, "enter dependency_check", chapter_run_id)

    async with async_session_factory() as db:
        outline_node = (await db.execute(
            select(OutlineNode).where(OutlineNode.id == outline_node_id)
        )).scalar_one_or_none()
        if not outline_node:
            await _set_chapter_status(chapter_id, ChapterState.FAILED.value, "outline node missing after dep check")
            return _result(PipelineOutcome.PERMANENT_FAILURE, error_code="outline_missing")
        outline_version_id = outline_node.outline_version_id

        deps_met, dep_errors = await check_required_dependencies(
            db, book_id, chapter_no, outline_version_id
        )
        if not deps_met:
            logger.warning(f"Chapter {chapter_no} blocked by dependencies: {dep_errors}")
            await db.commit()
            await _set_chapter_status(
                chapter_id,
                ChapterState.BLOCKED_BY_DEPENDENCY.value,
                f"deps: {dep_errors}",
            )
            return _result(
                PipelineOutcome.BLOCKED_DEPENDENCY,
                error_code="blocked_by_dependency",
                detail={"deps": dep_errors},
            )

        forced_deps = await dependency_resolver(db, book_id, outline_node_id)

        # Load L4 summary for query planning
        l4_summary = {}
        for char_id in outline_node.involved_character_ids[:5]:
            cid = uuid.UUID(char_id) if isinstance(char_id, str) else char_id
            snap = (await db.execute(
                select(MemoryL4StateSnapshot).where(
                    MemoryL4StateSnapshot.book_id == book_id,
                    MemoryL4StateSnapshot.entity_id == cid,
                ).order_by(MemoryL4StateSnapshot.as_of_chapter.desc()).limit(1)
            )).scalar_one_or_none()
            if snap:
                l4_summary[str(char_id)] = snap.state

        # Extract outline data for use outside session
        outline_data = {
            "id": str(outline_node_id),
            "chapter_no": outline_node.chapter_no,
            "goal": outline_node.goal,
            "required_beats": outline_node.required_beats,
            "forbidden_outcomes": outline_node.forbidden_outcomes,
            "involved_character_ids": outline_node.involved_character_ids,
            "plot_thread_ids": outline_node.plot_thread_ids,
            "depends_on": outline_node.depends_on,
            "expected_state_changes": getattr(outline_node, "expected_state_changes", None) or [],
            "title": outline_node.title,
            "target_char_range": [
                chapter_target.minimum_chars,
                chapter_target.maximum_chars,
            ],
        }

    # === Phase 2: QueryPlanner (checkpointed) ===
    async def _do_query_plan(_payload):
        qp = await query_planner_agent(
            book_id=book_id,
            outline_node={
                "chapter_no": outline_data["chapter_no"],
                "involved_character_ids": outline_data["involved_character_ids"],
                "plot_thread_ids": outline_data["plot_thread_ids"],
                "depends_on": outline_data["depends_on"],
            },
            scene_plan={},
            required_deps=forced_deps,
            l4_summary=json.dumps(l4_summary, ensure_ascii=False)[:2000],
            chapter_id=chapter_id,
            l4_refs=[{"entity_id": k} for k in l4_summary.keys()],
        )
        if qp is None:
            qp = deterministic_query_template(
                outline_node={
                    "involved_character_ids": outline_data["involved_character_ids"],
                    "plot_thread_ids": outline_data["plot_thread_ids"],
                },
                scene_plan={},
                required_deps=forced_deps,
                l4_st=l4_summary,
                current_chapter=chapter_no,
            )
            if isinstance(qp, dict):
                qp = {**qp, "source": "deterministic_fallback"}
            logger.info(f"QueryPlanner degraded for chapter {chapter_no}")
        return qp

    try:
        qp_art = await run_step(
            ctx=ctx,
            step_name="query_plan",
            step_key="query_plan",
            input_payload={
                "outline_version_id": str(outline_version_id),
                "outline_node_id": outline_data["id"],
                "forced_deps": forced_deps,
                "l4_keys": sorted(l4_summary.keys()),
            },
            execute_fn=_do_query_plan,
        )
        query_plan = qp_art.output if isinstance(qp_art.output, dict) else {}
        if qp_art.reused:
            logger.info(f"query_plan reused for chapter {chapter_no}")
    except ControlRequestedError as e:
        await _set_chapter_status(chapter_id, ChapterState.NEEDS_HUMAN.value if e.control == "pause" else ChapterState.FAILED.value, f"control:{e.control}", chapter_run_id)
        return _result(PipelineOutcome.PAUSED if e.control == "pause" else PipelineOutcome.PERMANENT_FAILURE, error_code=f"control_{e.control}")
    except LeaseLostError:
        return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="lease_lost")
    except PermanentStepError as e:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
        return _result(PipelineOutcome.PERMANENT_FAILURE, error_code=e.code, detail=e.detail)
    except RetryableStepError as e:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
        return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code=e.code, detail=e.detail)

    # Persist the planner output before retrieval starts. Internal audit fields
    # are removed from plan_json but retained as relational provenance.
    query_plan = dict(query_plan)
    source_run_id = query_plan.pop("_agent_run_id", None)
    prompt_version = query_plan.pop("_prompt_version", None)
    model_name = query_plan.pop("_model_name", None)
    try:
        source_run_id = uuid.UUID(str(source_run_id)) if source_run_id else None
    except (ValueError, AttributeError):
        source_run_id = None
    source_run_id = source_run_id or chapter_run_id or uuid.uuid4()
    plan_source = query_plan.get("source") or "agent"
    prompt_version = str(prompt_version or ("deterministic-fallback-v1" if plan_source == "deterministic_fallback" else "unknown"))
    model_name = str(model_name or ("deterministic" if plan_source == "deterministic_fallback" else "unknown"))

    async with async_session_factory() as db:
        query_plan_row = QueryPlan(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=chapter_id,
            plan_json=query_plan,
            source_run_id=source_run_id,
            prompt_version=prompt_version,
            model_name=model_name,
        )
        db.add(query_plan_row)
        await db.flush()
        retrieval_run = RetrievalRun(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=chapter_id,
            query_plan_id=query_plan_row.id,
            status="running",
            degraded=plan_source == "deterministic_fallback",
        )
        db.add(retrieval_run)
        await db.commit()

    # === Phase 3: Retrieval (SQL-first 9-step) ===
    await _set_chapter_status(chapter_id, ChapterState.CONTEXT_BUILDING.value, "enter context_building", chapter_run_id)

    async with async_session_factory() as db:
        char_ids = [uuid.UUID(c) if isinstance(c, str) else c
                    for c in query_plan.get("character_ids", outline_data["involved_character_ids"])]

        l4_states = await state_resolver(db, book_id, char_ids, chapter_no)
        await plot_thread_resolver(db, book_id, [])

        event_types = query_plan.get("event_types", []) or []
        chap_range = query_plan.get("chapter_range") or {"from": 1, "to": max(chapter_no - 1, 1)}
        if isinstance(chap_range, list):
            if len(chap_range) >= 2:
                chap_range = {"from": chap_range[0], "to": chap_range[1]}
            elif len(chap_range) == 1:
                chap_range = {"from": 1, "to": chap_range[0]}
            else:
                chap_range = {"from": 1, "to": max(chapter_no - 1, 1)}
        elif not isinstance(chap_range, dict):
            chap_range = {"from": 1, "to": max(chapter_no - 1, 1)}
        try:
            cr_from = int(chap_range.get("from", 1) or 1)
            cr_to = int(chap_range.get("to", max(chapter_no - 1, 1)) or max(chapter_no - 1, 1))
        except Exception:
            cr_from, cr_to = 1, max(chapter_no - 1, 1)
        event_candidates = await event_ledger_search(
            db, book_id, char_ids, event_types,
            (cr_from, cr_to)
        )

        # v9.1 §15: true causal frontier via StoryEventEdge BFS
        causal_nodes = await causal_frontier_step(db, book_id, query_plan, char_ids)
        if causal_nodes:
            existing = {c.get("event_id") for c in event_candidates}
            for node in causal_nodes:
                if node.get("event_id") not in existing:
                    event_candidates.append(node)

        search_terms = query_plan.get("exact_terms", []) or []
        if isinstance(search_terms, str):
            search_terms = [search_terms]
        ft_candidates = await full_text_search(
            db, book_id, search_terms,
            (cr_from, cr_to)
        )

    # Step 6: merge and score (pure computation, no DB)
    scored = candidate_merge_and_score(event_candidates, ft_candidates, query_plan)

    # Step 7: EvidenceRanker (LLM call — no session held)
    semantic_qs = query_plan.get("semantic_questions", [])
    ranked = await evidence_ranker_agent(
        book_id=book_id,
        candidates=scored,
        semantic_questions=semantic_qs,
        chapter_goal=outline_data["goal"],
        chapter_id=chapter_id,
    )
    retrieved_evidence = ranked[:8]

    async with async_session_factory() as db:
        db.add_all(build_retrieval_candidates(retrieval_run.id, scored, retrieved_evidence))
        retrieval_run_db = (await db.execute(
            select(RetrievalRun).where(RetrievalRun.id == retrieval_run.id)
        )).scalar_one()
        retrieval_run_db.status = "completed"
        retrieval_run_db.candidate_count = len(scored)
        retrieval_run_db.selected_count = len(retrieved_evidence)
        retrieval_run_db.latency_ms = 0
        await db.commit()

    # === Phase 4: ContextAssembler ===
    # v9.1: context is now assembled per scene inside the DraftWriter loop
    # (scoped via select_relevant_scene_state); here we only fetch + guard the
    # outline node each scene assembly needs.
    outline_node = await _get_outline_node(uuid.UUID(outline_data["id"]))
    if not outline_node:
        logger.error(f"Outline node disappeared for chapter {chapter_no}")
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, "outline disappeared")
        return _result(PipelineOutcome.PERMANENT_FAILURE, error_code="outline_disappeared")

    # === Phase 5: ChapterPlanner (checkpointed) ===
    await _set_chapter_status(chapter_id, ChapterState.PLANNING.value, "enter planning", chapter_run_id)

    async def _do_plan(_payload):
        return await plan_chapter(
            book_id=book_id,
            chapter_id=chapter_id,
            outline_node_id=uuid.UUID(outline_data["id"]),
            forced_dependencies=forced_deps,
            l4_states=l4_states,
            retrieved_evidence=retrieved_evidence,
            target_word_count=chapter_target.target_chars,
        )

    try:
        plan_art = await run_step(
            ctx=ctx,
            step_name="chapter_plan",
            step_key="chapter_plan",
            input_payload={
                "outline_node_id": outline_data["id"],
                "outline_version_id": str(outline_version_id),
                "evidence_ids": [
                    (e.get("id") or e.get("event_id") or e.get("paragraph_key") or str(i))
                    for i, e in enumerate(retrieved_evidence[:16])
                    if isinstance(e, dict)
                ],
                "target_word_count": chapter_target.target_chars,
            },
            execute_fn=_do_plan,
        )
        scene_plan = plan_art.output if isinstance(plan_art.output, dict) else None
        if plan_art.reused:
            logger.info(f"chapter_plan reused for chapter {chapter_no}")
    except ControlRequestedError as e:
        await _set_chapter_status(chapter_id, ChapterState.NEEDS_HUMAN.value if e.control == "pause" else ChapterState.FAILED.value, f"control:{e.control}", chapter_run_id)
        return _result(PipelineOutcome.PAUSED if e.control == "pause" else PipelineOutcome.PERMANENT_FAILURE, error_code=f"control_{e.control}")
    except LeaseLostError:
        return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="lease_lost")
    except PermanentStepError as e:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
        return _result(PipelineOutcome.PERMANENT_FAILURE, error_code=e.code, detail=e.detail)
    except RetryableStepError as e:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
        return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code=e.code)

    if not scene_plan:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, "planner empty", chapter_run_id)
        return _result(PipelineOutcome.PERMANENT_FAILURE, error_code="planner_empty")

    # Normalize scene_no to unique sequential 1..N (planner often returns all scene_no=1)
    raw_scenes = scene_plan.get("scenes") or []
    valid_scenes = [dict(scene) for scene in raw_scenes if isinstance(scene, dict)]
    if not valid_scenes:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, "no scenes in plan")
        return _result(PipelineOutcome.PERMANENT_FAILURE, error_code="planner_no_scenes")
    if len(valid_scenes) > 8:
        await _set_chapter_status(
            chapter_id,
            ChapterState.FAILED.value,
            "planner produced more than 8 scenes",
        )
        return _result(
            PipelineOutcome.PERMANENT_FAILURE,
            error_code="planner_too_many_scenes",
            detail={"scene_count": len(valid_scenes)},
        )
    normalized = []
    scene_targets = distribute_scene_targets(
        chapter_target.target_chars,
        [
            scene.get("target_word_count")
            for scene in valid_scenes
        ],
    )
    for idx, sc in enumerate(valid_scenes, start=1):
        sc["scene_no"] = idx
        sc["target_word_count"] = scene_targets[idx - 1]
        normalized.append(sc)
    scene_plan = {**scene_plan, "scenes": normalized}

    # === Phase 5.5: v9 CCNE — compile scene contracts (no LLM) ===
    involved_ids = list(outline_data.get("involved_character_ids") or [])
    try:
        from app.engine.causal_compile import load_states_and_anchors
        from app.engine.causal_errors import CausalHardBlockError

        _states_for_compile, _anchors_by_char = await load_states_and_anchors(
            book_id, involved_ids, chapter_no
        )
        if l4_states:
            for _cid, _st in l4_states.items():
                _states_for_compile.setdefault(str(_cid), _st)
        compile_result = await compile_chapter_contracts(
            book_id=book_id,
            chapter_id=chapter_id,
            chapter_no=chapter_no,
            scene_plan=scene_plan,
            l4_states=_states_for_compile,
            core_anchors_by_char=_anchors_by_char,
            outline_expected_effects=outline_data.get("expected_state_changes") or [],
            source_run_id=chapter_run_id,
        )
        scene_contracts = compile_result.get("contracts") or []
        logger.info(
            "v9 compiled %s scene contracts for chapter %s (blockers=%s)",
            len(scene_contracts), chapter_no, len(compile_result.get("blockers") or []),
        )
    except CausalHardBlockError as e:
        # v9.1 §3.3: hard constraint violations must never silently degrade
        logger.error("v9 contract hard block for chapter %s: %s", chapter_no, e)
        await _set_chapter_status(
            chapter_id, ChapterState.NEEDS_HUMAN.value,
            f"causal_compile_blocked:{e.code}", chapter_run_id,
        )
        return _result(
            PipelineOutcome.NEEDS_HUMAN,
            error_code="causal_compile_blocked",
            detail={"cause": e.code, "message": str(e)},
        )
    except Exception as e:
        # deterministic engine runtime error — retryable, not silent degradation
        logger.warning("v9 contract compile failed for chapter %s: %s", chapter_no, e)
        await _set_chapter_status(
            chapter_id, ChapterState.FAILED.value,
            "causal_compile_error", chapter_run_id,
        )
        return _result(
            PipelineOutcome.RETRYABLE_FAILURE,
            error_code="causal_compile_error",
            detail={"error": str(e)},
        )
    contract_by_scene = {
        int(c.get("scene_no") or 0): c for c in scene_contracts if isinstance(c, dict)
    }
    # v9.1: per-scene working state snapshots (state BEFORE each scene runs)
    working_states_by_scene: dict[int, dict] = compile_result.get("working_states_by_scene") or {}

    # === Phase 5.6: v9.1 Pre-Draft Contract Gate (spec §7.1) ===
    # Fail-closed: no scene may enter drafting while its contract has blockers.
    from app.engine.contract_gate import run_contract_gate

    cg = run_contract_gate(scene_contracts, compile_result.get("reports"))
    if not cg.ok:
        logger.error(
            "v9.1 contract gate blocked chapter %s: %s blocker(s)", chapter_no, len(cg.blockers)
        )
        await _set_chapter_status(
            chapter_id, ChapterState.NEEDS_HUMAN.value,
            "contract_gate_blocked", chapter_run_id,
        )
        return _result(
            PipelineOutcome.NEEDS_HUMAN,
            error_code="contract_gate_blocked",
            detail={"blockers": cg.blockers[:20], "warnings": cg.warnings[:20]},
        )
    logger.info(
        "v9.1 contract gate passed chapter %s (%s scenes, %s warnings)",
        chapter_no, cg.contracts_checked, len(cg.warnings),
    )

    # === Phase 6: DraftWriter (per scene, checkpointed) ===
    await _set_chapter_status(chapter_id, ChapterState.DRAFTING.value, "enter drafting", chapter_run_id)

    scene_contents = []
    previous_tail = ""
    previous_tail_hash = content_hash("")
    for scene_def in scene_plan.get("scenes", []):
        scene_no = int(scene_def.get("scene_no") or len(scene_contents) + 1)
        target_wc = scene_def.get("target_word_count", 2000)
        step_key = f"draft_scene:{scene_no}"

        # v9.1 per-scene context: this scene's contract + working-state snapshot
        scene_contract = contract_by_scene.get(scene_no)
        scene_working_state = working_states_by_scene.get(scene_no) or {}
        relevant_state = select_relevant_scene_state(
            scene_contract=scene_contract,
            l4_states=scene_working_state,
            core_anchors_by_char=_anchors_by_char,
        )

        async def _do_draft(
            _payload,
            _sd=scene_def,
            _twc=target_wc,
            _pt=previous_tail,
            _sc=scene_contract,
            _rs=relevant_state,
        ):
            # v9.1: each scene assembles its own context package scoped to the
            # characters/state slices it actually touches (spec §5/§6) — no
            # scene receives the whole chapter's L4 state.
            scene_style = None
            async with async_session_factory() as db:
                scene_context = await assemble_context(
                    db, book_id, outline_node, _sd, forced_deps,
                    retrieved_evidence, _pt, chapter_no,
                    scene_contract=_sc,
                    relevant_state=_rs,
                    agent_role="draft_writer",
                )
                # §47: optional scene style contract (best-effort, non-fatal)
                try:
                    from app.style.service import load_scene_style_contract

                    scene_style = await load_scene_style_contract(db, book_id, scene_no)
                except Exception as e:
                    logger.debug("scene style contract skipped: %s", e)
            content, error = await write_scene(
                book_id=book_id,
                chapter_id=chapter_id,
                scene_plan=_sd,
                context_package=scene_context,
                previous_scene_tail=_pt,
                target_word_count=_twc,
                scene_contract=_sc,
                scene_style_contract=scene_style,
            )
            scene_min = max(1, int(_twc * 0.85))
            scene_max = max(scene_min, int(_twc * 1.15))
            actual_chars = len((content or "").strip())
            if not error and not scene_min <= actual_chars <= scene_max:
                error = (
                    f"scene_length_contract:{actual_chars} outside "
                    f"{scene_min}..{scene_max}"
                )
            if error and not error.startswith("PIPELINE_BLOCKED"):
                logger.warning(f"Scene failed, retrying: {error}")
                retry_plan = {
                    **_sd,
                    "length_correction": (
                        f"上一稿为 {actual_chars} 字；本次必须完整重写并落在 "
                        f"{scene_min}..{scene_max} 字，不能续写、拼接或灌水。"
                    ),
                }
                content, error = await write_scene(
                    book_id=book_id,
                    chapter_id=chapter_id,
                    scene_plan=retry_plan,
                    context_package=scene_context,
                    previous_scene_tail=_pt,
                    target_word_count=_twc,
                    scene_contract=_sc,
                    scene_style_contract=scene_style,
                )
                actual_chars = len((content or "").strip())
                if not error and not scene_min <= actual_chars <= scene_max:
                    error = (
                        f"scene_length_contract:{actual_chars} outside "
                        f"{scene_min}..{scene_max} after retry"
                    )
            if error and error.startswith("PIPELINE_BLOCKED"):
                raise PermanentStepError("draft_blocked", {"error": error})
            if not content or error:
                raise RetryableStepError("scene_generation_failed", {"error": str(error), "scene_no": scene_no})
            return {
                "content": content,
                "scene_no": scene_no,
                "summary": _sd.get("goal", ""),
                "pov_character_id": _sd.get("pov_character_id"),
                "content_hash": content_hash(content),
            }

        try:
            art = await run_step(
                ctx=ctx,
                step_name="draft_scene",
                step_key=step_key,
                input_payload={
                    "scene_no": scene_no,
                    "scene_plan": scene_def,
                    "previous_scene_tail_hash": previous_tail_hash,
                    "target_word_count": target_wc,
                    "outline_node_id": outline_data["id"],
                },
                execute_fn=_do_draft,
            )
        except ControlRequestedError as e:
            await _set_chapter_status(
                chapter_id,
                ChapterState.NEEDS_HUMAN.value if e.control == "pause" else ChapterState.FAILED.value,
                f"control:{e.control}",
                chapter_run_id,
            )
            return _result(
                PipelineOutcome.PAUSED if e.control == "pause" else PipelineOutcome.PERMANENT_FAILURE,
                error_code=f"control_{e.control}",
            )
        except LeaseLostError:
            return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="lease_lost")
        except PermanentStepError as e:
            await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
            return _result(PipelineOutcome.PERMANENT_FAILURE, error_code=e.code, detail=e.detail)
        except RetryableStepError as e:
            await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
            return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code=e.code, detail=e.detail)

        payload = art.output if isinstance(art.output, dict) else {"content": art.output}
        content = payload.get("content") or ""
        if not content:
            await _set_chapter_status(chapter_id, ChapterState.FAILED.value, "empty scene content", chapter_run_id)
            return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="empty_scene")
        if art.reused:
            logger.info(f"draft_scene:{scene_no} reused for chapter {chapter_no}")

        scene_id = uuid.uuid4()
        scene_contents.append({
            "scene_no": scene_no,
            "content": content,
            "scene_id": str(scene_id),
            "summary": payload.get("summary") or scene_def.get("goal", ""),
            "pov_character_id": payload.get("pov_character_id") or scene_def.get("pov_character_id"),
            "step_reused": art.reused,
            "step_run_id": str(art.step_run_id) if art.step_run_id else None,
        })
        previous_tail = content[-500:]
        previous_tail_hash = content_hash(previous_tail)

    # Final safety: re-index scene_no uniquely
    for idx, sc in enumerate(scene_contents, start=1):
        sc["scene_no"] = idx

    chapter_content = "\n\n".join(s["content"] for s in scene_contents)
    word_count = len(chapter_content)
    source_run = uuid.uuid4()

    # === Phase 7: Persist scenes + chapter version + Review ===
    # B-05: versions append-only; never update existing version rows; supersede old draft scenes.
    async with async_session_factory() as db:
        from app.models import Scene, Paragraph
        import hashlib
        from sqlalchemy import func as sa_func

        max_ver = (
            await db.execute(
                select(sa_func.coalesce(sa_func.max(ChapterVersion.version), 0)).where(
                    ChapterVersion.chapter_id == chapter_id
                )
            )
        ).scalar()
        current_version = int(max_ver or 0) + 1

        # Supersede previous draft scenes (do not delete history)
        old_scenes = (
            await db.execute(
                select(Scene).where(
                    Scene.chapter_id == chapter_id,
                    Scene.canon_status == "draft",
                )
            )
        ).scalars().all()
        for s in old_scenes:
            s.canon_status = "superseded"

        outline_node_uuid = uuid.UUID(outline_data["id"])
        for sc in scene_contents:
            # NOTE: do not name this `content_hash` — that shadows the imported helper
            # and causes UnboundLocalError on earlier content_hash(...) calls in this fn.
            scene_content_hash = hashlib.sha256(sc["content"].encode("utf-8")).hexdigest()
            scene_row = Scene(
                id=uuid.UUID(sc["scene_id"]),
                book_id=book_id,
                chapter_id=chapter_id,
                scene_no=sc["scene_no"],
                outline_node_id=outline_node_uuid,
                content=sc["content"],
                content_hash=scene_content_hash,
                canon_status="draft",
                version=current_version,
            )
            if sc.get("pov_character_id"):
                try:
                    scene_row.pov_character_id = (
                        uuid.UUID(sc["pov_character_id"])
                        if isinstance(sc["pov_character_id"], str)
                        else sc["pov_character_id"]
                    )
                except Exception:
                    pass
            db.add(scene_row)

            paras = [p for p in sc["content"].split("\n\n") if p.strip()]
            for pi, para_text in enumerate(paras, start=1):
                para_hash = hashlib.sha256(para_text.encode("utf-8")).hexdigest()
                para = Paragraph(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chapter_id=chapter_id,
                    scene_id=uuid.UUID(sc["scene_id"]),
                    paragraph_key=f"p-{sc['scene_no']:02d}-{pi:04d}",
                    ordinal=pi,
                    content=para_text,
                    content_hash=para_hash,
                    version=current_version,
                )
                db.add(para)

        # Append-only ChapterVersion (never UPDATE existing rows)
        ch_version = ChapterVersion(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=chapter_id,
            version=current_version,
            content=chapter_content,
            word_count=word_count,
            source_run_id=source_run,
            version_kind="draft",
            content_hash=hashlib.sha256(chapter_content.encode("utf-8")).hexdigest(),
            chapter_run_id=chapter_run_id,
        )
        db.add(ch_version)
        await db.commit()

    await _set_chapter_status(chapter_id, ChapterState.REVIEWING.value, "enter reviewing", chapter_run_id)

    async def _do_review(_payload):
        p, iss = await review_chapter(
            book_id=book_id,
            chapter_id=chapter_id,
            chapter_content=chapter_content,
            outline_data=outline_data,
            outline_node_id=uuid.UUID(outline_data["id"]),
            scene_contracts=scene_contracts,
        )
        return {"passed": bool(p), "issues": iss or []}

    try:
        rev_art = await run_step(
            ctx=ctx,
            step_name="review",
            step_key=f"review:0:{content_hash(chapter_content)[:16]}",
            input_payload={
                "content_hash": content_hash(chapter_content),
                "outline_node_id": outline_data["id"],
                "word_count": word_count,
            },
            execute_fn=_do_review,
        )
        rev = rev_art.output if isinstance(rev_art.output, dict) else {}
        passed = bool(rev.get("passed"))
        issues = rev.get("issues") or []
        length_issues = chapter_length_issues(chapter_content, chapter_target)
        if length_issues:
            passed = False
            existing_issue_ids = {
                issue.get("issue_id") for issue in issues if isinstance(issue, dict)
            }
            issues.extend(
                issue for issue in length_issues if issue["issue_id"] not in existing_issue_ids
            )

        # §51: StyleVerifier — deterministic style findings enter the patch loop
        try:
            from app.style.service import get_latest_profile
            from app.style.verifier import build_style_patch_issue, verify_draft_style

            async with async_session_factory() as db:
                _prof = await get_latest_profile(db, book_id)
                if _prof is not None and _prof.metric_ranges:
                    _style = verify_draft_style(chapter_content, _prof.metric_ranges)
                    for _f in _style.get("findings", []):
                        issues.append(build_style_patch_issue(_f))
        except Exception as _e:  # non-fatal: style verify never blocks the pipeline
            logger.debug("style verify skipped: %s", _e)
    except ControlRequestedError as e:
        await _set_chapter_status(chapter_id, ChapterState.NEEDS_HUMAN.value if e.control == "pause" else ChapterState.FAILED.value, f"control:{e.control}", chapter_run_id)
        return _result(PipelineOutcome.PAUSED if e.control == "pause" else PipelineOutcome.PERMANENT_FAILURE, error_code=f"control_{e.control}")
    except LeaseLostError:
        return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="lease_lost")
    except PermanentStepError as e:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
        return _result(PipelineOutcome.PERMANENT_FAILURE, error_code=e.code, detail=e.detail)
    except RetryableStepError as e:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
        return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code=e.code)

    # === Phase 8: Patching (if issues) ===
    # P1 QA-001: drafts may be kept; finalize is never soft-passed.
    def _has_service_error(iss: list) -> bool:
        return any(
            isinstance(i, dict)
            and i.get("severity") == "critical"
            and i.get("category") == "service_error"
            for i in (iss or [])
        )

    if not passed and issues:
        # Review agent / provider outage: fail closed for finalize path
        if _has_service_error(issues):
            logger.error(
                f"Review service failure for chapter {chapter_no} (no soft-pass): {issues}"
            )
            await _set_chapter_status(
                chapter_id,
                ChapterState.FAILED.value,
                "review service_error fail-closed",
            )
            return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="review_service_error")

        await _set_chapter_status(chapter_id, ChapterState.PATCHING.value, "enter patching", chapter_run_id)

        clusters = {}
        for issue in issues:
            cid = issue.get("issue_cluster_id", issue.get("issue_id"))
            clusters.setdefault(cid, []).append(issue)

        remaining = issues
        for _cluster_id, cluster_issues in clusters.items():
            for retry_round in range(1, 3):  # max 2 auto patch rounds (v2.0)
                # INV-12: pause/cancel at patch step boundary
                async def _do_patch_round(_payload, _issues=cluster_issues, _round=retry_round, _content=chapter_content):
                    patches = []
                    for issue in _issues:
                        if issue.get("severity") == "critical":
                            continue
                        patch = await generate_patch(
                            book_id=book_id,
                            chapter_id=chapter_id,
                            issue=issue,
                            chapter_content=_content,
                            retry_round=_round,
                        )
                        if patch:
                            patches.append(patch)
                    new_content = _content
                    applied = 0
                    if patches:
                        try:
                            new_content = await apply_patches(_content, patches)
                            applied = len(patches)
                        except PatchStaleError as e:
                            raise PermanentStepError("PATCH_STALE", {"message": str(e)})
                    return {
                        "chapter_content": new_content,
                        "applied": applied,
                        "patch_count": len(patches),
                        "content_hash": content_hash(new_content),
                    }

                try:
                    patch_art = await run_step(
                        ctx=ctx,
                        step_name="patch_round",
                        step_key=f"patch:r{retry_round}:{content_hash(chapter_content)[:12]}:{str(_cluster_id)[:20]}",
                        input_payload={
                            "round": retry_round,
                            "cluster_id": str(_cluster_id),
                            "content_hash": content_hash(chapter_content),
                            "issue_ids": [i.get("issue_id") for i in cluster_issues],
                        },
                        execute_fn=_do_patch_round,
                    )
                except ControlRequestedError as e:
                    await _set_chapter_status(
                        chapter_id,
                        ChapterState.NEEDS_HUMAN.value if e.control == "pause" else ChapterState.FAILED.value,
                        f"control:{e.control}",
                        chapter_run_id,
                    )
                    return _result(
                        PipelineOutcome.PAUSED if e.control == "pause" else PipelineOutcome.PERMANENT_FAILURE,
                        error_code=f"control_{e.control}",
                    )
                except LeaseLostError:
                    return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="lease_lost")
                except PermanentStepError as e:
                    if e.code == "PATCH_STALE":
                        await _set_chapter_status(
                            chapter_id, ChapterState.NEEDS_HUMAN.value, f"PATCH_STALE: {e.detail}", chapter_run_id
                        )
                        return _result(PipelineOutcome.NEEDS_HUMAN, error_code="PATCH_STALE", detail=e.detail)
                    await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
                    return _result(PipelineOutcome.PERMANENT_FAILURE, error_code=e.code, detail=e.detail)

                payload = patch_art.output if isinstance(patch_art.output, dict) else {}
                new_content = payload.get("chapter_content") or chapter_content
                if new_content != chapter_content:
                    chapter_content = new_content
                    word_count = len(chapter_content)
                    current_version += 1
                    import hashlib as _hl
                    async with async_session_factory() as db:
                        patched_ver = ChapterVersion(
                            id=uuid.uuid4(),
                            book_id=book_id,
                            chapter_id=chapter_id,
                            version=current_version,
                            content=chapter_content,
                            word_count=word_count,
                            source_run_id=source_run,
                            version_kind="patched",
                            content_hash=_hl.sha256(chapter_content.encode("utf-8")).hexdigest(),
                            chapter_run_id=chapter_run_id,
                        )
                        db.add(patched_ver)
                        await db.commit()

                # Re-review after patch (checkpointed)
                async def _do_rereview(_payload, _content=chapter_content):
                    p, iss = await review_chapter(
                        book_id=book_id,
                        chapter_id=chapter_id,
                        chapter_content=_content,
                        outline_data=outline_data,
                        outline_node_id=uuid.UUID(outline_data["id"]),
                        scene_contracts=scene_contracts,
                    )
                    return {"passed": bool(p), "issues": iss or []}

                try:
                    rr_art = await run_step(
                        ctx=ctx,
                        step_name="review",
                        step_key=f"review:r{retry_round}:{content_hash(chapter_content)[:16]}",
                        input_payload={
                            "content_hash": content_hash(chapter_content),
                            "round": retry_round,
                            "after_patch": True,
                        },
                        execute_fn=_do_rereview,
                    )
                except ControlRequestedError as e:
                    await _set_chapter_status(
                        chapter_id,
                        ChapterState.NEEDS_HUMAN.value if e.control == "pause" else ChapterState.FAILED.value,
                        f"control:{e.control}",
                        chapter_run_id,
                    )
                    return _result(
                        PipelineOutcome.PAUSED if e.control == "pause" else PipelineOutcome.PERMANENT_FAILURE,
                        error_code=f"control_{e.control}",
                    )
                except LeaseLostError:
                    return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="lease_lost")
                except PermanentStepError as e:
                    await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
                    return _result(PipelineOutcome.PERMANENT_FAILURE, error_code=e.code, detail=e.detail)
                except RetryableStepError as e:
                    await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
                    return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code=e.code)

                rev = rr_art.output if isinstance(rr_art.output, dict) else {}
                passed = bool(rev.get("passed"))
                remaining = rev.get("issues") or []
                length_issues = chapter_length_issues(chapter_content, chapter_target)
                if length_issues:
                    passed = False
                    existing_issue_ids = {
                        issue.get("issue_id")
                        for issue in remaining
                        if isinstance(issue, dict)
                    }
                    remaining.extend(
                        issue
                        for issue in length_issues
                        if issue["issue_id"] not in existing_issue_ids
                    )
                if passed or not remaining:
                    break
                if _has_service_error(remaining):
                    logger.error(
                        f"Re-review service error chapter {chapter_no} fail-closed"
                    )
                    await _set_chapter_status(
                        chapter_id,
                        ChapterState.FAILED.value,
                        "re-review service_error fail-closed",
                        chapter_run_id,
                    )
                    return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="review_service_error")

        if not passed:
            await _set_chapter_status(
                chapter_id,
                ChapterState.NEEDS_HUMAN.value,
                f"review not passed after patch; remaining={len(remaining or issues or [])}",
            )
            logger.warning(
                f"Chapter {chapter_no} needs human intervention after patch rounds"
            )
            return _result(
                PipelineOutcome.NEEDS_HUMAN,
                error_code="review_needs_human",
                detail={"remaining": len(remaining or issues or [])},
            )
    elif not passed:
        # Review failed with empty issues / unusable payload — fail closed
        logger.error(
            f"Chapter {chapter_no}: review empty-fail fail-closed (wc={word_count})"
        )
        await _set_chapter_status(
            chapter_id,
            ChapterState.FAILED.value,
            "review empty-fail fail-closed",
        )
        return _result(PipelineOutcome.PERMANENT_FAILURE, error_code="review_empty_fail")

    # === Phase 9: Mechanical consistency gate (B-08) + StateExtractor ===
    await _set_chapter_status(chapter_id, ChapterState.CONSISTENCY_CHECK.value, "mechanical consistency", chapter_run_id)
    from app.engine.mechanical_gate import run_mechanical_consistency

    async def _do_consistency(_payload):
        res = run_mechanical_consistency(
            chapter_content=chapter_content,
            scenes=scene_contents,
            outline_data=outline_data,
            scene_plan=scene_plan if isinstance(scene_plan, dict) else {},
            scene_contracts=contract_by_scene,
            pre_states_by_scene=working_states_by_scene,
            pre_state=l4_states or {},
            core_anchors=[
                {"anchor_code": a.get("anchor_code"), "statement": a.get("statement")}
                for anchors in (_anchors_by_char or {}).values()
                for a in anchors
            ] if _anchors_by_char else None,
            min_chars=chapter_target.minimum_chars,
            max_chars=chapter_target.maximum_chars,
        )
        return res.as_dict()

    try:
        cons_art = await run_step(
            ctx=ctx,
            step_name="consistency_check",
            step_key=f"consistency:{content_hash(chapter_content)[:16]}",
            input_payload={
                "content_hash": content_hash(chapter_content),
                "scene_count": len(scene_contents),
                "word_count": word_count,
            },
            execute_fn=_do_consistency,
        )
        cons = cons_art.output if isinstance(cons_art.output, dict) else {}
        if not cons.get("ok", False):
            hard = [f for f in (cons.get("findings") or []) if f.get("severity") in ("blocker", "major")]
            logger.error("consistency_check failed chapter %s: %s", chapter_no, hard[:5])
            await _set_chapter_status(
                chapter_id,
                ChapterState.NEEDS_HUMAN.value if hard else ChapterState.FAILED.value,
                "consistency_failed",
                chapter_run_id,
            )
            return _result(
                PipelineOutcome.NEEDS_HUMAN if hard else PipelineOutcome.PERMANENT_FAILURE,
                error_code="consistency_failed",
                detail={"findings": cons.get("findings") or []},
            )
    except ControlRequestedError as e:
        await _set_chapter_status(
            chapter_id,
            ChapterState.NEEDS_HUMAN.value if e.control == "pause" else ChapterState.FAILED.value,
            f"control:{e.control}",
            chapter_run_id,
        )
        return _result(
            PipelineOutcome.PAUSED if e.control == "pause" else PipelineOutcome.PERMANENT_FAILURE,
            error_code=f"control_{e.control}",
        )
    except LeaseLostError:
        return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="lease_lost")
    except Exception as e:
        logger.exception("consistency_check error")
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, f"consistency_error:{e}", chapter_run_id)
        return _result(PipelineOutcome.PERMANENT_FAILURE, error_code="consistency_error", detail={"error": str(e)})

    await _set_chapter_status(chapter_id, ChapterState.STATE_EXTRACTING.value, "enter extract", chapter_run_id)

    # Load L4 DTOs in short session
    async with async_session_factory() as db:
        current_l4 = {}
        for char_id in outline_data.get("involved_character_ids", [])[:5]:
            cid = uuid.UUID(char_id) if isinstance(char_id, str) else char_id
            snap = (await db.execute(
                select(MemoryL4StateSnapshot).where(
                    MemoryL4StateSnapshot.book_id == book_id,
                    MemoryL4StateSnapshot.entity_id == cid,
                ).order_by(MemoryL4StateSnapshot.as_of_chapter.desc()).limit(1)
            )).scalar_one_or_none()
            if snap:
                current_l4[str(char_id)] = snap.state

    # Phase 9b: Extractor candidates ONLY (B-06) — no canon writes here
    from app.engine.final_artifact import build_final_artifact, SCENE_JOIN
    from app.engine.step_runner import run_step as _run_step, ControlRequestedError as _Ctrl, LeaseLostError as _Lease, RetryableStepError as _Retry

    # Rebuild scene list if patch flattened content with scene join
    scenes_for_final = list(scene_contents)
    if scene_contents and chapter_content:
        parts = chapter_content.split(SCENE_JOIN)
        if len(parts) == len(scene_contents):
            scenes_for_final = [
                {**sc, "content": parts[i]} for i, sc in enumerate(scene_contents)
            ]
        elif len(scene_contents) == 1:
            scenes_for_final = [{**scene_contents[0], "content": chapter_content}]
        # else keep original scene contents; integrity check will fail closed if mismatch

    # Attach paragraph keys for extractor evidence
    art_preview = build_final_artifact(scenes_for_final)
    scenes_with_paras = []
    for sc_art in art_preview.scenes:
        scenes_with_paras.append({
            "scene_no": sc_art.scene_no,
            "content": sc_art.content,
            "summary": sc_art.summary,
            "paragraphs": [
                {"paragraph_key": p.paragraph_key, "content": p.content}
                for p in sc_art.paragraphs
            ],
        })

    async def _do_extract(_payload):
        ok, events, errors, extras = await extract_candidates(
            book_id=book_id,
            chapter_id=chapter_id,
            chapter_no=chapter_no,
            chapter_content=chapter_content,
            scenes=scenes_with_paras,
            outline_node=outline_data,
            current_l4=current_l4,
            scene_contracts=scene_contracts,
            core_anchors=[
                a
                for anchors in (_anchors_by_char or {}).values()
                for a in anchors
            ] if _anchors_by_char else None,
        )
        if not ok:
            raise _Retry("state_extract_failed", {"errors": errors})
        return {"events": events, "errors": errors, "extras": extras}

    try:
        from app.engine.final_artifact import sha256_text as _sha
        extract_art = await _run_step(
            ctx=ctx,
            step_name="canon_extract",
            step_key=f"canon_extract:{_sha(chapter_content)[:16]}",
            input_payload={
                "content_hash": _sha(chapter_content),
                "outline_node_id": outline_data["id"],
                "scene_count": len(scenes_for_final),
            },
            execute_fn=_do_extract,
        )
        extract_payload = extract_art.output if isinstance(extract_art.output, dict) else {}
        candidate_events = extract_payload.get("events") or []
    except _Ctrl as e:
        await _set_chapter_status(
            chapter_id,
            ChapterState.NEEDS_HUMAN.value if e.control == "pause" else ChapterState.FAILED.value,
            f"control:{e.control}",
            chapter_run_id,
        )
        return _result(
            PipelineOutcome.PAUSED if e.control == "pause" else PipelineOutcome.PERMANENT_FAILURE,
            error_code=f"control_{e.control}",
        )
    except _Lease:
        return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="lease_lost")
    except _Retry as e:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
        return _result(PipelineOutcome.PERMANENT_FAILURE, error_code=e.code, detail=e.detail)

    # === Phase 9c: v9.1 Post-Draft Realization Gate (spec §7.2) ===
    # Verify the actual extracted events realize the compiled contracts
    # BEFORE canon commit. Fail-closed: blocker/major → NEEDS_HUMAN.
    from app.engine.realization_gate import run_realization_gate

    async def _do_realization_gate(_payload):
        extras = extract_payload.get("extras") or {}
        rg = run_realization_gate(
            scene_contracts=scene_contracts,
            actual_events=candidate_events,
            reaction_evidence=extras.get("reaction_evidence") or [],
            attributions=extras.get("attributions") or [],
        )
        return rg.as_dict()

    try:
        rg_art = await _run_step(
            ctx=ctx,
            step_name="realization_gate",
            step_key=f"realization:{_sha(chapter_content)[:16]}",
            input_payload={
                "content_hash": _sha(chapter_content),
                "contract_count": len(scene_contracts),
                "event_count": len(candidate_events),
            },
            execute_fn=_do_realization_gate,
        )
        rg = rg_art.output if isinstance(rg_art.output, dict) else {}
        if not rg.get("ok", False):
            rg_hard = [
                f for f in (rg.get("findings") or [])
                if f.get("severity") in ("blocker", "major")
            ]
            logger.error(
                "realization gate failed chapter %s: %s hard finding(s), summary=%s",
                chapter_no, len(rg_hard), rg.get("summary"),
            )
            await _set_chapter_status(
                chapter_id, ChapterState.NEEDS_HUMAN.value,
                "realization_gate_failed", chapter_run_id,
            )
            return _result(
                PipelineOutcome.NEEDS_HUMAN,
                error_code="realization_gate_failed",
                detail={
                    "findings": rg_hard[:20],
                    "summary": rg.get("summary") or {},
                },
            )
    except _Ctrl as e:
        await _set_chapter_status(
            chapter_id,
            ChapterState.NEEDS_HUMAN.value if e.control == "pause" else ChapterState.FAILED.value,
            f"control:{e.control}",
            chapter_run_id,
        )
        return _result(
            PipelineOutcome.PAUSED if e.control == "pause" else PipelineOutcome.PERMANENT_FAILURE,
            error_code=f"control_{e.control}",
        )
    except _Lease:
        return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="lease_lost")
    except _Retry as e:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
        return _result(PipelineOutcome.PERMANENT_FAILURE, error_code=e.code, detail=e.detail)

    # === Phase 10: Atomic Finalize + Canon (single transaction) ===
    from app.engine.chapter_finalizer import commit_final_chapter_snapshot, FinalScene
    from app.engine.final_artifact import build_final_artifact as _bfa

    final_artifact = _bfa(scenes_for_final, joined_content=None)
    # If pipeline chapter_content is the joined form with SCENE_JOIN, prefer it only when equal
    if chapter_content == final_artifact.joined_content:
        pass
    elif len(scenes_for_final) == 1:
        final_artifact = _bfa(
            [{**scenes_for_final[0], "content": chapter_content}],
        )
    # else: multi-scene must match join; do not squash (B-06 / §8.2)

    final_scenes = [
        FinalScene(
            scene_no=sc.scene_no,
            content=sc.content,
            scene_id=uuid.UUID(sc.scene_id) if sc.scene_id else None,
            summary=sc.summary or "",
            pov_character_id=uuid.UUID(sc.pov_character_id) if sc.pov_character_id else None,
        )
        for sc in final_artifact.scenes
    ]

    snap = await commit_final_chapter_snapshot(
        book_id=book_id,
        chapter_id=chapter_id,
        expected_previous_version=current_version,
        final_artifact=final_artifact,
        final_content=final_artifact.joined_content,
        final_scenes=final_scenes,
        validated_events=candidate_events,
        source_run_ids=[source_run],
        chapter_run_id=chapter_run_id,
        outline_node_id=uuid.UUID(outline_data["id"]),
        outline_version_id=outline_version_id,
        title=outline_data.get("title") or f"Chapter {chapter_no}",
        chapter_no=chapter_no,
        pipeline_version=PIPELINE_VERSION,
        worker_id=worker_id,
        scene_contracts=scene_contracts,
    )
    if not snap.ok:
        logger.error(f"Final snapshot failed for chapter {chapter_no}: {snap.error}")
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, f"finalize: {snap.error}")
        return _result(
            PipelineOutcome.RETRYABLE_FAILURE,
            error_code="finalize_failed",
            detail={"error": snap.error},
        )
    logger.info(
        f"Chapter {chapter_no} finalized: {snap.word_count} words v{snap.version}"
    )

    # === Phase 11: MilestoneTrigger ===
    # L2 windows are volume-local (10 chapters, with a shorter tail at volume
    # end); L3 is generated exactly at the volume boundary.  This prevents a
    # summary such as 11-20 from mixing two different volume strategies.
    async with async_session_factory() as db:
        volume = (
            await db.execute(
                select(OutlineVolume).where(
                    OutlineVolume.book_id == book_id,
                    OutlineVolume.outline_version_id == outline_version_id,
                    OutlineVolume.chapter_from <= chapter_no,
                    OutlineVolume.chapter_to >= chapter_no,
                )
            )
        ).scalar_one_or_none()
        outline_version_no = (
            await db.execute(
                select(OutlineVersion.version).where(OutlineVersion.id == outline_version_id)
            )
        ).scalar_one_or_none()
        outline_version_no = int(outline_version_no or 1)
        if volume is not None:
            stage_start, stage_end = volume_stage_window(
                chapter_no,
                int(volume.chapter_from),
                int(volume.chapter_to),
            )
            if chapter_no == stage_end:
                await generate_l2(
                    db,
                    book_id,
                    stage_start,
                    stage_end,
                    outline_version=outline_version_no,
                )
                await db.commit()
            if chapter_no == int(volume.chapter_to):
                await generate_l3(
                    db,
                    book_id,
                    int(volume.volume_no),
                    outline_version=outline_version_no,
                    chapter_start=int(volume.chapter_from),
                    chapter_end=int(volume.chapter_to),
                )
                await db.commit()
        elif chapter_no % 10 == 0:
            await generate_l2(
                db,
                book_id,
                chapter_no - 9,
                chapter_no,
                outline_version=outline_version_no,
            )
            await db.commit()

    if chapter_no % 30 == 0:
        async with async_session_factory() as db:
            await run_drift_audit(db, book_id, chapter_no - 29, chapter_no)
            await db.commit()

    return _result(
        PipelineOutcome.FINALIZED,
        final_version=snap.version,
        detail={"word_count": snap.word_count},
    )
