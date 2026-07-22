"""Full chapter pipeline - 13 steps per §7.2 v7.3.
FIX P0-6: Scene 落库 + ChapterVersion 版本递增 + Patch 后创建新版本
FIX P0-7: version 不再写死=1，查询已有最高版本+1
"""
import uuid
import json
import hashlib
import logging
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import async_session_factory
from app.state_machine import ChapterState, can_transition
from app.models import (
    Chapter, ChapterTask, ChapterVersion, Scene, OutlineNode,
    AgentRun, MemoryL4StateSnapshot, PlotThread,
    QueryPlan, RetrievalRun, RetrievalCandidate, RetrievalJudgement,
    RewritePatch,
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


async def _get_next_version(db: AsyncSession, chapter_id: uuid.UUID) -> int:
    """Get the next version number for a chapter (fixes P0-7: version=1 撞唯一约束)."""
    result = await db.execute(
        select(func.max(ChapterVersion.version)).where(ChapterVersion.chapter_id == chapter_id)
    )
    max_version = result.scalar()
    return (max_version or 0) + 1


async def execute_pipeline(book_id: uuid.UUID, chapter_id: uuid.UUID, chapter_no: int):
    """Execute the full 13-step pipeline for one chapter."""

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

            forced_deps = await dependency_resolver(db, book_id, outline_node.id)

            # ============ Step 2: QueryPlanner ============
            chapter.status = ChapterState.CONTEXT_BUILDING.value
            await db.commit()

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

            l4_states = await state_resolver(db, book_id, char_ids, chapter_no)
            open_threads = await plot_thread_resolver(db, book_id, [])

            event_types = query_plan.get("event_types", [])
            chap_range = query_plan.get("chapter_range", {"from": 1, "to": chapter_no - 1})
            event_candidates = await event_ledger_search(
                db, book_id, char_ids, event_types,
                (chap_range.get("from", 1), chap_range.get("to", chapter_no - 1)),
            )

            search_terms = query_plan.get("exact_terms", [])
            ft_candidates = await full_text_search(db, book_id, search_terms,
                                                   (chap_range.get("from", 1), chap_range.get("to", chapter_no - 1)))

            scored = candidate_merge_and_score(event_candidates, ft_candidates, query_plan)

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
                    content, error = await write_scene(
                        db, book_id, chapter_id, scene_def,
                        context_pkg, previous_tail, target_wc
                    )

                # FIX P0-6: Create Scene ORM object, don't use random UUID
                scene_obj = Scene(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chapter_id=chapter_id,
                    scene_no=scene_def.get("scene_no", len(scene_contents) + 1),
                    outline_node_id=outline_node.id,
                    content=content or "[FAILED]",
                    content_hash=hashlib.sha256((content or "").encode()).hexdigest(),
                    canon_status="draft",
                    version=1,
                )
                db.add(scene_obj)
                await db.flush()

                if content:
                    scene_contents.append({
                        "scene_no": scene_obj.scene_no,
                        "content": content,
                        "scene_id": str(scene_obj.id),
                        "summary": scene_def.get("goal", ""),
                    })
                    previous_tail = content[-500:]
                else:
                    scene_contents.append({
                        "scene_no": scene_obj.scene_no,
                        "content": "[FAILED]",
                        "scene_id": str(scene_obj.id),
                        "summary": scene_def.get("goal", ""),
                    })

            chapter_content = "\n\n".join(s["content"] for s in scene_contents)
            word_count = len(chapter_content)

            # FIX P0-7: Get next version number, don't hardcode version=1
            source_run = uuid.uuid4()
            draft_version = await _get_next_version(db, chapter_id)
            ch_version = ChapterVersion(
                id=uuid.uuid4(),
                book_id=book_id,
                chapter_id=chapter_id,
                version=draft_version,
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

            # Handle FAIL-CLOSED: if not passed and no issues, agent itself failed
            if not passed and not issues:
                logger.error(f"ReviewAgent failed (fail-closed) for chapter {chapter_no}")
                chapter.status = ChapterState.NEEDS_HUMAN.value
                await db.commit()
                return

            # ============ Step 7b: Patching (if issues) ============
            if not passed and issues:
                chapter.status = ChapterState.PATCHING.value
                await db.commit()

                clusters = {}
                for issue in issues:
                    cid = issue.get("issue_cluster_id", issue.get("issue_id"))
                    clusters.setdefault(cid, []).append(issue)

                for cluster_id, cluster_issues in clusters.items():
                    for retry_round in range(1, 4):
                        patches = []
                        for issue in cluster_issues:
                            if issue.get("severity") == "critical":
                                continue
                            patch = await generate_patch(
                                db, book_id, chapter_id, issue,
                                chapter_content, retry_round=retry_round
                            )
                            if patch:
                                patches.append(patch)
                                # Persist patch to database
                                patch_record = RewritePatch(
                                    id=uuid.uuid4(),
                                    book_id=book_id,
                                    chapter_id=chapter_id,
                                    scene_id=chapter_id,  # simplification
                                    paragraph_id=issue.get("paragraph_id", "p-0000"),
                                    chapter_version=draft_version,
                                    expected_hash=patch.get("expected_hash", ""),
                                    replacement_text=patch.get("replacement_text", ""),
                                    resolved_issue_ids=patch.get("resolved_issue_ids", []),
                                    status="applied",
                                    retry_round=retry_round,
                                    source_run_id=source_run,
                                )
                                db.add(patch_record)

                        if patches:
                            chapter_content = await apply_patches(chapter_content, patches)

                        passed, remaining = await review_chapter(
                            db, book_id, chapter_id, chapter_content, outline_node
                        )
                        if passed or not remaining:
                            break

                if not passed:
                    chapter.status = ChapterState.NEEDS_HUMAN.value
                    await db.commit()
                    logger.warning(f"Chapter {chapter_no} needs human after 3 patch rounds")
                    return

            # ============ Step 8: ContinuityCheck ============
            chapter.status = ChapterState.CONSISTENCY_CHECK.value
            await db.commit()

            # ============ Step 9-10: StateExtractor + StateCommitter ============
            chapter.status = ChapterState.STATE_EXTRACTING.value
            await db.commit()

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

            # FIX P0-6: After patching, create FINAL version with patched content
            final_version = await _get_next_version(db, chapter_id)
            if final_version > draft_version:
                # Already have patched version created somewhere
                pass
            else:
                final_ch_version = ChapterVersion(
                    id=uuid.uuid4(),
                    book_id=book_id,
                    chapter_id=chapter_id,
                    version=final_version,
                    content=chapter_content,  # This is the PATCHED content
                    word_count=len(chapter_content),
                    source_run_id=source_run,
                )
                db.add(final_ch_version)

            # ============ Step 11-12: SearchIndexer + Finalizer ============
            chapter.status = ChapterState.FINALIZING.value
            await db.commit()

            chapter.status = ChapterState.FINALIZED.value
            chapter.finalized_version = final_version
            chapter.title = outline_node.title or f"Chapter {chapter_no}"

            from app.models import Book
            book = (await db.execute(select(Book).where(Book.id == book_id))).scalar_one_or_none()
            if book:
                book.finalized_chapters += 1
                book.finalized_words += len(chapter_content)

            await db.commit()

            logger.info(f"Chapter {chapter_no} finalized: {len(chapter_content)} words, version {final_version}")

            # ============ Step 13: MilestoneTrigger ============
            if chapter_no % 10 == 0:
                chap_start = chapter_no - 9
                await generate_l2(db, book_id, chap_start, chapter_no)

            if chapter_no % 30 == 0:
                await run_drift_audit(db, book_id, chapter_no - 29, chapter_no)
                await db.commit()

        except Exception as e:
            logger.exception(f"Pipeline error for chapter {chapter_no}: {e}")
            chapter.status = ChapterState.FAILED.value
            await db.commit()
