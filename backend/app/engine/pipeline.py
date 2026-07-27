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
    Chapter, ChapterVersion, OutlineNode,
    MemoryL4StateSnapshot,
)
from app.engine.outline import check_required_dependencies
from app.engine.retrieval import (
    dependency_resolver, state_resolver, plot_thread_resolver,
    event_ledger_search, full_text_search, candidate_merge_and_score,
    evidence_ranker_agent, query_planner_agent, deterministic_query_template
)
from app.engine.context_assembler import assemble_context
from app.agents.chapter_planner import plan_chapter
from app.agents.draft_writer import write_scene
from app.agents.review_agent import review_chapter
from app.agents.patch_editor import generate_patch, apply_patches, PatchStaleError
from app.agents.state_extractor import extract_candidates
from app.agents.drift_audit import run_drift_audit
from app.engine.memory_compiler import generate_l2
from app.engine.outcomes import PipelineOutcome, PipelineResult
from app.engine.step_runner import (
    RunContext, run_step, canonical_hash, content_hash,
    ControlRequestedError, LeaseLostError, RetryableStepError, PermanentStepError,
    PIPELINE_VERSION,
)
import os, socket

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

        await db.commit()

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
            "title": outline_node.title,
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
    except RetryableStepError as e:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
        return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code=e.code, detail=e.detail)

    # === Phase 3: Retrieval (SQL-first 9-step) ===
    await _set_chapter_status(chapter_id, ChapterState.CONTEXT_BUILDING.value, "enter context_building", chapter_run_id)

    async with async_session_factory() as db:
        char_ids = [uuid.UUID(c) if isinstance(c, str) else c
                    for c in query_plan.get("character_ids", outline_data["involved_character_ids"])]

        l4_states = await state_resolver(db, book_id, char_ids, chapter_no)
        open_threads = await plot_thread_resolver(db, book_id, [])

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

    # === Phase 4: ContextAssembler ===
    async with async_session_factory() as db:
        outline_node = await _get_outline_node(uuid.UUID(outline_data["id"]))
        if not outline_node:
            logger.error(f"Outline node disappeared for chapter {chapter_no}")
            await _set_chapter_status(chapter_id, ChapterState.FAILED.value, "outline disappeared")
            return _result(PipelineOutcome.PERMANENT_FAILURE, error_code="outline_disappeared")

        context_pkg = await assemble_context(
            db, book_id, outline_node, {}, forced_deps,
            retrieved_evidence, "", chapter_no
        )

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
            target_word_count=3000,
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
                "target_word_count": 3000,
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
    except RetryableStepError as e:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, e.code, chapter_run_id)
        return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code=e.code)

    if not scene_plan:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, "planner empty", chapter_run_id)
        return _result(PipelineOutcome.PERMANENT_FAILURE, error_code="planner_empty")

    # Normalize scene_no to unique sequential 1..N (planner often returns all scene_no=1)
    raw_scenes = scene_plan.get("scenes") or []
    if not raw_scenes:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value, "no scenes in plan")
        return _result(PipelineOutcome.PERMANENT_FAILURE, error_code="planner_no_scenes")
    normalized = []
    for idx, sc in enumerate(raw_scenes, start=1):
        if not isinstance(sc, dict):
            continue
        sc = dict(sc)
        sc["scene_no"] = idx
        sc.setdefault("target_word_count", max(800, int(3000 / max(len(raw_scenes), 1))))
        normalized.append(sc)
    scene_plan = {**scene_plan, "scenes": normalized}

    # === Phase 6: DraftWriter (per scene, checkpointed) ===
    await _set_chapter_status(chapter_id, ChapterState.DRAFTING.value, "enter drafting", chapter_run_id)

    scene_contents = []
    previous_tail = ""
    previous_tail_hash = content_hash("")
    for scene_def in scene_plan.get("scenes", []):
        scene_no = int(scene_def.get("scene_no") or len(scene_contents) + 1)
        target_wc = scene_def.get("target_word_count", 2000)
        step_key = f"draft_scene:{scene_no}"

        async def _do_draft(_payload, _sd=scene_def, _twc=target_wc, _pt=previous_tail):
            content, error = await write_scene(
                book_id=book_id,
                chapter_id=chapter_id,
                scene_plan=_sd,
                context_package=context_pkg,
                previous_scene_tail=_pt,
                target_word_count=_twc,
            )
            if error and not error.startswith("PIPELINE_BLOCKED"):
                logger.warning(f"Scene failed, retrying: {error}")
                content, error = await write_scene(
                    book_id=book_id,
                    chapter_id=chapter_id,
                    scene_plan=_sd,
                    context_package=context_pkg,
                    previous_scene_tail=_pt,
                    target_word_count=_twc,
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
            content_hash = hashlib.sha256(sc["content"].encode("utf-8")).hexdigest()
            scene_row = Scene(
                id=uuid.UUID(sc["scene_id"]),
                book_id=book_id,
                chapter_id=chapter_id,
                scene_no=sc["scene_no"],
                outline_node_id=outline_node_uuid,
                content=sc["content"],
                content_hash=content_hash,
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
    except ControlRequestedError as e:
        await _set_chapter_status(chapter_id, ChapterState.NEEDS_HUMAN.value if e.control == "pause" else ChapterState.FAILED.value, f"control:{e.control}", chapter_run_id)
        return _result(PipelineOutcome.PAUSED if e.control == "pause" else PipelineOutcome.PERMANENT_FAILURE, error_code=f"control_{e.control}")
    except LeaseLostError:
        return _result(PipelineOutcome.RETRYABLE_FAILURE, error_code="lease_lost")
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

        await _set_chapter_status(chapter_id, ChapterState.PATCHING.value, "enter patching")

        clusters = {}
        for issue in issues:
            cid = issue.get("issue_cluster_id", issue.get("issue_id"))
            clusters.setdefault(cid, []).append(issue)

        remaining = issues
        for _cluster_id, cluster_issues in clusters.items():
            for retry_round in range(1, 3):  # max 2 auto patch rounds (v2.0)
                patches = []
                for issue in cluster_issues:
                    if issue.get("severity") == "critical":
                        continue  # Critical issues go to NEEDS_HUMAN
                    patch = await generate_patch(
                        book_id=book_id,
                        chapter_id=chapter_id,
                        issue=issue,
                        chapter_content=chapter_content,
                        retry_round=retry_round,
                    )
                    if patch:
                        patches.append(patch)

                if patches:
                    try:
                        chapter_content = await apply_patches(chapter_content, patches)
                    except PatchStaleError as e:
                        logger.error(f"PATCH_STALE chapter {chapter_no}: {e}")
                        await _set_chapter_status(
                            chapter_id,
                            ChapterState.NEEDS_HUMAN.value,
                            f"PATCH_STALE: {e}",
                        )
                        return _result(
                            PipelineOutcome.NEEDS_HUMAN,
                            error_code="PATCH_STALE",
                            detail={"message": str(e)},
                        )
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

                # Re-review after patch (no session held during LLM)
                passed, remaining = await review_chapter(
                    book_id=book_id,
                    chapter_id=chapter_id,
                    chapter_content=chapter_content,
                    outline_data=outline_data,
                    outline_node_id=uuid.UUID(outline_data["id"]),
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

    # === Phase 9: ContinuityCheck + StateExtractor ===
    await _set_chapter_status(chapter_id, ChapterState.CONSISTENCY_CHECK.value)
    await _set_chapter_status(chapter_id, ChapterState.STATE_EXTRACTING.value)

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
        ok, events, errors = await extract_candidates(
            book_id=book_id,
            chapter_id=chapter_id,
            chapter_no=chapter_no,
            chapter_content=chapter_content,
            scenes=scenes_with_paras,
            outline_node=outline_data,
            current_l4=current_l4,
        )
        if not ok:
            raise _Retry("state_extract_failed", {"errors": errors})
        return {"events": events, "errors": errors}

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
    if chapter_no % 10 == 0:
        chap_start = chapter_no - 9
        async with async_session_factory() as db:
            await generate_l2(db, book_id, chap_start, chapter_no)

    if chapter_no % 30 == 0:
        async with async_session_factory() as db:
            await run_drift_audit(db, book_id, chapter_no - 29, chapter_no)
            await db.commit()

    return _result(
        PipelineOutcome.FINALIZED,
        final_version=snap.version,
        detail={"word_count": snap.word_count},
    )
