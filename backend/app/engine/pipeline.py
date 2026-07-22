"""Full chapter pipeline - 13 steps per §7.2 v7.3.
Replaces the stub in arq_worker.py.
"""
import uuid
import json
import hashlib
import logging
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import async_session_factory
from app.state_machine import ChapterState, can_transition
from app.models import (
    Chapter, ChapterTask, ChapterVersion, Scene, OutlineNode,
    AgentRun, MemoryL4StateSnapshot, PlotThread
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
from app.engine.memory_compiler import generate_l2, generate_l3

logger = logging.getLogger("novelforge.pipeline")


async def execute_pipeline(book_id: uuid.UUID, chapter_id: uuid.UUID, chapter_no: int):
    """Execute the full 13-step pipeline for one chapter."""
    
    async with async_session_factory() as db:
        chapter = (await db.execute(
            select(Chapter).where(Chapter.id == chapter_id)
        )).scalar_one_or_none()
        if not chapter:
            logger.error(f"Chapter {chapter_id} not found")
            return

        # Get outline node
        outline_node = (await db.execute(
            select(OutlineNode).where(OutlineNode.id == chapter.outline_node_id)
        )).scalar_one_or_none()
        if not outline_node:
            logger.error(f"Outline node not found for chapter {chapter_no}")
            chapter.status = ChapterState.FAILED.value
            await db.commit()
            return

        try:
            # ============ Step 1: DependencyGate ============
            chapter.status = ChapterState.DEPENDENCY_CHECK.value
            await db.commit()

            deps_met, dep_errors = await check_required_dependencies(
                db, book_id, chapter_no, outline_node.outline_version_id
            )
            if not deps_met:
                logger.warning(f"Chapter {chapter_no} blocked by dependencies: {dep_errors}")
                chapter.status = ChapterState.BLOCKED_BY_DEPENDENCY.value
                await db.commit()
                return

            # Get forced dependencies
            forced_deps = await dependency_resolver(db, book_id, outline_node.id)

            # ============ Step 2: QueryPlanner ============
            chapter.status = ChapterState.CONTEXT_BUILDING.value
            await db.commit()

            # Get L4 summary for query planning
            l4_summary = {}
            for char_id in outline_node.involved_character_ids[:5]:  # Limit to 5
                cid = uuid.UUID(char_id) if isinstance(char_id, str) else char_id
                snap = (await db.execute(
                    select(MemoryL4StateSnapshot).where(
                        MemoryL4StateSnapshot.book_id == book_id,
                        MemoryL4StateSnapshot.entity_id == cid,
                    ).order_by(MemoryL4StateSnapshot.as_of_chapter.desc()).limit(1)
                )).scalar_one_or_none()
                if snap:
                    l4_summary[str(char_id)] = snap.state

            query_plan = await query_planner_agent(
                outline_node={
                    "chapter_no": outline_node.chapter_no,
                    "involved_character_ids": outline_node.involved_character_ids,
                    "plot_thread_ids": outline_node.plot_thread_ids,
                    "depends_on": outline_node.depends_on,
                },
                scene_plan={},
                required_deps=forced_deps,
                l4_summary=json.dumps(l4_summary, ensure_ascii=False)[:2000],
            )

            if query_plan is None:
                # Deterministic fallback per §6.4
                query_plan = deterministic_query_template(
                    outline_node={
                        "involved_character_ids": outline_node.involved_character_ids,
                        "plot_thread_ids": outline_node.plot_thread_ids,
                    },
                    scene_plan={},
                    required_deps=forced_deps,
                    l4_st=l4_summary,
                    current_chapter=chapter_no,
                )
                logger.info(f"QueryPlanner degraded for chapter {chapter_no}")

            # ============ Step 3: Retrieval (SQL-first 9-step) ============
            char_ids = [uuid.UUID(c) if isinstance(c, str) else c
                        for c in query_plan.get("character_ids", outline_node.involved_character_ids)]
            
            # Step 1-3: deps, state, threads (forced)
            l4_states = await state_resolver(db, book_id, char_ids, chapter_no)
            open_threads = await plot_thread_resolver(db, book_id, [])

            # Step 4-5: event ledger + full text search
            event_types = query_plan.get("event_types", [])
            chap_range = query_plan.get("chapter_range", {"from": 1, "to": chapter_no - 1})
            event_candidates = await event_ledger_search(
                db, book_id, char_ids, event_types,
                (chap_range.get("from", 1), chap_range.get("to", chapter_no - 1))
            )
            
            search_terms = query_plan.get("exact_terms", [])
            ft_candidates = await full_text_search(db, book_id, search_terms,
                                                   (chap_range.get("from", 1), chap_range.get("to", chapter_no - 1)))

            # Step 6: merge and score
            scored = candidate_merge_and_score(event_candidates, ft_candidates, query_plan)

            # Step 7: EvidenceRanker (LLM on Top 24)
            semantic_qs = query_plan.get("semantic_questions", [])
            ranked = await evidence_ranker_agent(scored, semantic_qs, outline_node.goal)
            retrieved_evidence = ranked[:8]

            # ============ Step 4: ContextAssembler ============
            context_pkg = await assemble_context(
                db, book_id, outline_node, {}, forced_deps,
                retrieved_evidence, "", chapter_no
            )

            # ============ Step 5: ChapterPlanner ============
            chapter.status = ChapterState.PLANNING.value
            await db.commit()

            scene_plan = await plan_chapter(
                db, book_id, chapter_id, outline_node,
                forced_deps, l4_states, target_word_count=3000
            )
            if not scene_plan:
                chapter.status = ChapterState.FAILED.value
                await db.commit()
                return

            # ============ Step 6: DraftWriter (per scene) ============
            chapter.status = ChapterState.DRAFTING.value
            await db.commit()

            scene_contents = []
            previous_tail = ""
            for scene_def in scene_plan.get("scenes", []):
                target_wc = scene_def.get("target_word_count", 2000)
                content, error = await write_scene(
                    db, book_id, chapter_id, scene_def,
                    context_pkg, previous_tail, target_wc
                )
                if error:
                    if error.startswith("PIPELINE_BLOCKED"):
                        logger.error(f"DraftWriter blocked: {error}")
                        chapter.status = ChapterState.FAILED.value
                        await db.commit()
                        return
                    logger.warning(f"Scene failed, retrying: {error}")
                    # Retry once
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

            # Assemble chapter content
            chapter_content = "\n\n".join(s["content"] for s in scene_contents)
            word_count = len(chapter_content)

            # Save chapter version
            source_run = uuid.uuid4()
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

            # ============ Step 7+8: ReviewAgent / ContinuityJudge ============
            chapter.status = ChapterState.REVIEWING.value
            await db.commit()

            passed, issues = await review_chapter(
                db, book_id, chapter_id, chapter_content, outline_node
            )

            # ============ Step 7b: Patching (if issues) ============
            if not passed and issues:
                chapter.status = ChapterState.PATCHING.value
                await db.commit()

                # Group by issue_cluster_id, apply 3-round rule per §8.3
                clusters = {}
                for issue in issues:
                    cid = issue.get("issue_cluster_id", issue.get("issue_id"))
                    clusters.setdefault(cid, []).append(issue)

                for cluster_id, cluster_issues in clusters.items():
                    for retry_round in range(1, 4):  # §8.3: max 3 rounds
                        patches = []
                        for issue in cluster_issues:
                            if issue.get("severity") == "critical":
                                continue  # Critical issues go to NEEDS_HUMAN
                            patch = await generate_patch(
                                db, book_id, chapter_id, issue,
                                chapter_content, retry_round=retry_round
                            )
                            if patch:
                                patches.append(patch)

                        if patches:
                            chapter_content = await apply_patches(chapter_content, patches)
                        
                        # Re-review after patch
                        passed, remaining = await review_chapter(
                            db, book_id, chapter_id, chapter_content, outline_node
                        )
                        if passed or not remaining:
                            break
                
                if not passed:
                    # §8.3: 3 rounds failed -> NEEDS_HUMAN
                    chapter.status = ChapterState.NEEDS_HUMAN.value
                    await db.commit()
                    logger.warning(f"Chapter {chapter_no} needs human intervention after 3 patch rounds")
                    return

            # ============ Step 8: ContinuityCheck (implicit in review) ============
            chapter.status = ChapterState.CONSISTENCY_CHECK.value
            await db.commit()

            # ============ Step 9-10: StateExtractor + StateCommitter ============
            chapter.status = ChapterState.STATE_EXTRACTING.value
            await db.commit()

            # Get current L4 for conflict detection
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
                chapter.status = ChapterState.FAILED.value
                await db.commit()
                logger.error(f"State extraction failed for chapter {chapter_no}: {conflicts}")
                return

            # ============ Step 11-12: SearchIndexer + Finalizer ============
            chapter.status = ChapterState.FINALIZING.value
            await db.commit()

            # Update chapter status
            chapter.status = ChapterState.FINALIZED.value
            chapter.finalized_version = 1
            chapter.title = outline_node.title or f"Chapter {chapter_no}"

            # Update book stats
            from app.models import Book
            book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one_or_none()
            if book:
                book.finalized_chapters += 1
                book.finalized_words += word_count

            await db.commit()

            logger.info(f"Chapter {chapter_no} finalized: {word_count} words")

            # ============ Step 13: MilestoneTrigger ============
            # L2 every 10 chapters
            if chapter_no % 10 == 0:
                chap_start = chapter_no - 9
                await generate_l2(db, book_id, chap_start, chapter_no)

            # L3 at volume end (TODO: detect from outline)
            # DriftAudit every 30 chapters
            if chapter_no % 30 == 0:
                await run_drift_audit(db, book_id, chapter_no - 29, chapter_no)
                await db.commit()

        except Exception as e:
            logger.exception(f"Pipeline error for chapter {chapter_no}: {e}")
            chapter.status = ChapterState.FAILED.value
            await db.commit()
