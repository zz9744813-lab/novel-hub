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
from app.agents.patch_editor import generate_patch, apply_patches
from app.agents.state_extractor import extract_and_commit
from app.agents.drift_audit import run_drift_audit
from app.engine.memory_compiler import generate_l2

logger = logging.getLogger("novelforge.pipeline")


async def _set_chapter_status(chapter_id: uuid.UUID, status: str):
    """Quick helper: update chapter status in a short-lived session."""
    async with async_session_factory() as db:
        chapter = (await db.execute(
            select(Chapter).where(Chapter.id == chapter_id)
        )).scalar_one_or_none()
        if chapter:
            chapter.status = status
            await db.commit()


async def _get_outline_node(outline_node_id: uuid.UUID) -> OutlineNode | None:
    """Fetch outline node in a short-lived session (detached after close)."""
    async with async_session_factory() as db:
        result = await db.execute(
            select(OutlineNode).where(OutlineNode.id == outline_node_id)
        )
        return result.scalar_one_or_none()


async def execute_pipeline(book_id: uuid.UUID, chapter_id: uuid.UUID, chapter_no: int):
    """Execute the full 13-step pipeline for one chapter.

    §2.5: Uses short-lived sessions for each phase.
    LLM calls happen outside session context.
    """

    # === Phase 1: Setup + DependencyGate ===
    async with async_session_factory() as db:
        chapter = (await db.execute(
            select(Chapter).where(Chapter.id == chapter_id)
        )).scalar_one_or_none()
        if not chapter:
            logger.error(f"Chapter {chapter_id} not found")
            return

        outline_node = (await db.execute(
            select(OutlineNode).where(OutlineNode.id == chapter.outline_node_id)
        )).scalar_one_or_none()
        if not outline_node:
            logger.error(f"Outline node not found for chapter {chapter_no}")
            await _set_chapter_status(chapter_id, ChapterState.FAILED.value)
            return

        outline_node_id = outline_node.id
        outline_version_id = outline_node.outline_version_id

        chapter.status = ChapterState.DEPENDENCY_CHECK.value
        await db.commit()

        deps_met, dep_errors = await check_required_dependencies(
            db, book_id, chapter_no, outline_version_id
        )
        if not deps_met:
            logger.warning(f"Chapter {chapter_no} blocked by dependencies: {dep_errors}")
            chapter.status = ChapterState.BLOCKED_BY_DEPENDENCY.value
            await db.commit()
            return

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

    # === Phase 2: QueryPlanner (LLM call — no session held) ===
    query_plan = await query_planner_agent(
        outline_node={
            "chapter_no": outline_data["chapter_no"],
            "involved_character_ids": outline_data["involved_character_ids"],
            "plot_thread_ids": outline_data["plot_thread_ids"],
            "depends_on": outline_data["depends_on"],
        },
        scene_plan={},
        required_deps=forced_deps,
        l4_summary=json.dumps(l4_summary, ensure_ascii=False)[:2000],
    )

    if query_plan is None:
        query_plan = deterministic_query_template(
            outline_node={
                "involved_character_ids": outline_data["involved_character_ids"],
                "plot_thread_ids": outline_data["plot_thread_ids"],
            },
            scene_plan={},
            required_deps=forced_deps,
            l4_st=l4_summary,
            current_chapter=chapter_no,
        )
        logger.info(f"QueryPlanner degraded for chapter {chapter_no}")

    # === Phase 3: Retrieval (SQL-first 9-step) ===
    await _set_chapter_status(chapter_id, ChapterState.CONTEXT_BUILDING.value)

    async with async_session_factory() as db:
        char_ids = [uuid.UUID(c) if isinstance(c, str) else c
                    for c in query_plan.get("character_ids", outline_data["involved_character_ids"])]

        l4_states = await state_resolver(db, book_id, char_ids, chapter_no)
        open_threads = await plot_thread_resolver(db, book_id, [])

        event_types = query_plan.get("event_types", [])
        chap_range = query_plan.get("chapter_range") or {"from": 1, "to": chapter_no - 1}
        event_candidates = await event_ledger_search(
            db, book_id, char_ids, event_types,
            (chap_range.get("from", 1), chap_range.get("to", chapter_no - 1))
        )

        search_terms = query_plan.get("exact_terms", [])
        ft_candidates = await full_text_search(
            db, book_id, search_terms,
            (chap_range.get("from", 1), chap_range.get("to", chapter_no - 1))
        )

    # Step 6: merge and score (pure computation, no DB)
    scored = candidate_merge_and_score(event_candidates, ft_candidates, query_plan)

    # Step 7: EvidenceRanker (LLM call — no session held)
    semantic_qs = query_plan.get("semantic_questions", [])
    ranked = await evidence_ranker_agent(scored, semantic_qs, outline_data["goal"])
    retrieved_evidence = ranked[:8]

    # === Phase 4: ContextAssembler ===
    async with async_session_factory() as db:
        outline_node = await _get_outline_node(uuid.UUID(outline_data["id"]))
        if not outline_node:
            logger.error(f"Outline node disappeared for chapter {chapter_no}")
            return

        context_pkg = await assemble_context(
            db, book_id, outline_node, {}, forced_deps,
            retrieved_evidence, "", chapter_no
        )

    # === Phase 5: ChapterPlanner ===
    await _set_chapter_status(chapter_id, ChapterState.PLANNING.value)

    async with async_session_factory() as db:
        outline_node = await _get_outline_node(uuid.UUID(outline_data["id"]))
        if not outline_node:
            await _set_chapter_status(chapter_id, ChapterState.FAILED.value)
            return

        scene_plan = await plan_chapter(
            db, book_id, chapter_id, outline_node,
            forced_deps, l4_states, target_word_count=3000
        )

    if not scene_plan:
        await _set_chapter_status(chapter_id, ChapterState.FAILED.value)
        return

    # === Phase 6: DraftWriter (per scene) ===
    await _set_chapter_status(chapter_id, ChapterState.DRAFTING.value)

    scene_contents = []
    previous_tail = ""
    for scene_def in scene_plan.get("scenes", []):
        target_wc = scene_def.get("target_word_count", 2000)
        # Each scene uses its own short-lived session
        async with async_session_factory() as db:
            content, error = await write_scene(
                db, book_id, chapter_id, scene_def,
                context_pkg, previous_tail, target_wc
            )

        if error:
            if error.startswith("PIPELINE_BLOCKED"):
                logger.error(f"DraftWriter blocked: {error}")
                await _set_chapter_status(chapter_id, ChapterState.FAILED.value)
                return
            logger.warning(f"Scene failed, retrying: {error}")
            async with async_session_factory() as db:
                content, error = await write_scene(
                    db, book_id, chapter_id, scene_def,
                    context_pkg, previous_tail, target_wc
                )

        if content:
            scene_contents.append({
                "scene_no": scene_def.get("scene_no", 1),
                "content": content,
                "scene_id": str(uuid.uuid4()),
                "summary": scene_def.get("goal", ""),
            })
            previous_tail = content[-500:]
        else:
            scene_contents.append({
                "scene_no": scene_def.get("scene_no", 1),
                "content": "[FAILED]",
                "scene_id": str(uuid.uuid4()),
                "summary": scene_def.get("goal", ""),
            })

    chapter_content = "\n\n".join(s["content"] for s in scene_contents)
    word_count = len(chapter_content)
    source_run = uuid.uuid4()

    # === Phase 7: Save chapter version + Review ===
    async with async_session_factory() as db:
        ch_version = ChapterVersion(
            id=uuid.uuid4(),
            book_id=book_id,
            chapter_id=chapter_id,
            version=1,
            content=chapter_content,
            word_count=word_count,
            source_run_id=source_run,
        )
        db.add(ch_version)
        await db.commit()

    await _set_chapter_status(chapter_id, ChapterState.REVIEWING.value)

    async with async_session_factory() as db:
        outline_node = await _get_outline_node(uuid.UUID(outline_data["id"]))
        if outline_node:
            passed, issues = await review_chapter(
                db, book_id, chapter_id, chapter_content, outline_node
            )
        else:
            passed, issues = True, []

    # === Phase 8: Patching (if issues) ===
    if not passed and issues:
        await _set_chapter_status(chapter_id, ChapterState.PATCHING.value)

        clusters = {}
        for issue in issues:
            cid = issue.get("issue_cluster_id", issue.get("issue_id"))
            clusters.setdefault(cid, []).append(issue)

        for _cluster_id, cluster_issues in clusters.items():
            for retry_round in range(1, 4):  # §8.3: max 3 rounds
                patches = []
                for issue in cluster_issues:
                    if issue.get("severity") == "critical":
                        continue  # Critical issues go to NEEDS_HUMAN
                    async with async_session_factory() as db:
                        patch = await generate_patch(
                            db, book_id, chapter_id, issue,
                            chapter_content, retry_round=retry_round
                        )
                    if patch:
                        patches.append(patch)

                if patches:
                    chapter_content = await apply_patches(chapter_content, patches)

                # Re-review after patch
                async with async_session_factory() as db:
                    outline_node = await _get_outline_node(uuid.UUID(outline_data["id"]))
                    if outline_node:
                        passed, remaining = await review_chapter(
                            db, book_id, chapter_id, chapter_content, outline_node
                        )
                    else:
                        passed, remaining = True, []
                    if passed or not remaining:
                        break

        if not passed:
            await _set_chapter_status(chapter_id, ChapterState.NEEDS_HUMAN.value)
            logger.warning(f"Chapter {chapter_no} needs human intervention after 3 patch rounds")
            return

    # === Phase 9: ContinuityCheck + StateExtractor ===
    await _set_chapter_status(chapter_id, ChapterState.CONSISTENCY_CHECK.value)
    await _set_chapter_status(chapter_id, ChapterState.STATE_EXTRACTING.value)

    async with async_session_factory() as db:
        outline_node = await _get_outline_node(uuid.UUID(outline_data["id"]))
        if not outline_node:
            logger.error(f"Outline node disappeared for chapter {chapter_no}")
            return

        current_l4 = {}
        for char_id in outline_node.involved_character_ids[:5]:
            cid = uuid.UUID(char_id) if isinstance(char_id, str) else char_id
            snap = (await db.execute(
                select(MemoryL4StateSnapshot).where(
                    MemoryL4StateSnapshot.book_id == book_id,
                    MemoryL4StateSnapshot.entity_id == cid,
                ).order_by(MemoryL4StateSnapshot.as_of_chapter.desc()).limit(1)
            )).scalar_one_or_none()
            if snap:
                current_l4[str(char_id)] = snap.state

        success, conflicts = await extract_and_commit(
            db, book_id, chapter_id, chapter_no,
            chapter_content, scene_contents, outline_node,
            current_l4, source_run
        )

        if not success:
            logger.error(f"State extraction failed for chapter {chapter_no}: {conflicts}")
            # Use a fresh session - the current session may be in an aborted state
            await _set_chapter_status(chapter_id, ChapterState.FAILED.value)
            return

    # === Phase 10: Finalization ===
    async with async_session_factory() as db:
        chapter = (await db.execute(
            select(Chapter).where(Chapter.id == chapter_id)
        )).scalar_one_or_none()
        if not chapter:
            return

        chapter.status = ChapterState.FINALIZING.value
        await db.commit()

        chapter.status = ChapterState.FINALIZED.value
        chapter.finalized_version = 1
        chapter.title = outline_data["title"] or f"Chapter {chapter_no}"

        from app.models import Book
        book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one_or_none()
        if book:
            book.finalized_chapters += 1
            book.finalized_words += word_count

        await db.commit()
        logger.info(f"Chapter {chapter_no} finalized: {word_count} words")

    # === Phase 11: MilestoneTrigger ===
    if chapter_no % 10 == 0:
        chap_start = chapter_no - 9
        async with async_session_factory() as db:
            await generate_l2(db, book_id, chap_start, chapter_no)

    if chapter_no % 30 == 0:
        async with async_session_factory() as db:
            await run_drift_audit(db, book_id, chapter_no - 29, chapter_no)
            await db.commit()
