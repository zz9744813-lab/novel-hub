"""v8 Phase2 multi-step import pipeline — checkpointed artifacts, no formal Book until commit."""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.engine.document_blocks import sha256_bytes
from app.engine.entity_resolver import (
    build_preview_bundle,
    detect_outline_conflicts,
    deterministic_outline_from_text,
    deterministic_world_from_text,
    merge_outline,
    merge_world,
    resolve_characters,
)
from app.engine.import_llm import call_import_agent
from app.models.tables import (
    Book,
    BookProfile,
    CharacterCard,
    CharacterRelationship,
    ImportArtifact,
    ImportConflict,
    ImportSession,
    ImportSessionEvent,
    LocationCard,
    OutlineNode,
    OutlineVersion,
    OutlineVolume,
    PlotThread,
    WritingConstraint,
    WorldRule,
    BookSource,
)

logger = logging.getLogger("novelforge.import_pipeline")

STEPS = [
    "classify",
    "sanitize_llm",
    "metadata",
    "world",
    "characters",
    "relationships",
    "outline",
    "plots",
    "writing_rules",
    "audit",
    "preview",
]

STEP_PROGRESS = {
    "classify": 0.40,
    "sanitize_llm": 0.48,
    "metadata": 0.55,
    "world": 0.62,
    "characters": 0.70,
    "relationships": 0.76,
    "outline": 0.84,
    "plots": 0.88,
    "writing_rules": 0.92,
    "audit": 0.96,
    "preview": 1.0,
}


def _utcnow():
    return datetime.now(timezone.utc)


def gen_uuid():
    return uuid.uuid4()


def _blocks_text(blocks: list[dict], limit: int = 28000) -> str:
    lines = []
    for b in blocks:
        bid = b.get("id") or b.get("block_id") or ""
        t = (b.get("text") or "").strip()
        if not t:
            continue
        lines.append(f"[{bid}] {t}")
    text = "\n".join(lines)
    if len(text) > limit:
        return text[:limit] + "\n…[truncated]"
    return text


async def _transition(db: AsyncSession, sess: ImportSession, to_status: str, step: str | None = None, detail: dict | None = None):
    db.add(
        ImportSessionEvent(
            id=gen_uuid(),
            import_session_id=sess.id,
            from_status=sess.status,
            to_status=to_status,
            step=step,
            detail=detail or {},
        )
    )
    sess.status = to_status
    sess.current_step = step
    sess.updated_at = _utcnow()


async def _save_artifact(
    db: AsyncSession,
    session_id: uuid.UUID,
    *,
    artifact_type: str,
    key: str,
    payload: dict,
    status: str = "ready",
    meta: list | dict | None = None,
) -> ImportArtifact:
    refs: list = []
    if isinstance(meta, list):
        refs = meta
    elif isinstance(meta, dict):
        refs = [meta]
    existing = (
        await db.execute(
            select(ImportArtifact)
            .where(
                ImportArtifact.import_session_id == session_id,
                ImportArtifact.artifact_key == key,
            )
            .order_by(ImportArtifact.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        existing.output_json = payload
        existing.status = status
        existing.artifact_type = artifact_type
        if meta is not None:
            existing.source_refs = refs
        return existing
    art = ImportArtifact(
        id=gen_uuid(),
        import_session_id=session_id,
        artifact_type=artifact_type,
        artifact_key=key,
        version=1,
        status=status,
        output_json=payload,
        source_refs=refs,
    )
    db.add(art)
    return art


async def _get_art(db: AsyncSession, session_id: uuid.UUID, key: str) -> dict | None:
    row = (
        await db.execute(
            select(ImportArtifact)
            .where(
                ImportArtifact.import_session_id == session_id,
                ImportArtifact.artifact_key == key,
            )
            .order_by(ImportArtifact.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row.output_json if row else None


async def _load_blocks(db: AsyncSession, sess: ImportSession) -> list[dict]:
    art = await _get_art(db, sess.id, "raw_blocks")
    if art and art.get("blocks"):
        return art["blocks"]
    source = (
        await db.execute(select(BookSource).where(BookSource.id == sess.source_id))
    ).scalar_one_or_none()
    if source and source.extracted_blocks_json:
        return (source.extracted_blocks_json or {}).get("blocks") or []
    return []


async def run_import_pipeline(session_id: str) -> dict:
    """Worker entry: multi-step LLM extract with per-step artifact checkpoint."""
    sid = uuid.UUID(session_id)
    report: dict[str, Any] = {"session_id": session_id, "steps": []}

    async with async_session_factory() as db:
        sess = (
            await db.execute(select(ImportSession).where(ImportSession.id == sid))
        ).scalar_one_or_none()
        if not sess:
            return {"error": "not_found"}
        if sess.status in ("completed", "committing"):
            return {"error": "already_committed", "status": sess.status}
        if sess.status == "analyzing" and sess.current_step in STEPS:
            # resume allowed
            pass
        await _transition(db, sess, "analyzing", "pipeline_start")
        sess.progress = 0.38
        await db.commit()

    # Load blocks once
    async with async_session_factory() as db:
        sess = (
            await db.execute(select(ImportSession).where(ImportSession.id == sid))
        ).scalar_one_or_none()
        blocks = await _load_blocks(db, sess)
        sanitize_cand = await _get_art(db, sid, "sanitize_v1") or {}
        cand_items = sanitize_cand.get("items") or []
        if cand_items and len(cand_items) == len(blocks):
            kept_blocks = [
                b for b, c in zip(blocks, cand_items) if c.get("action") != "exclude"
            ]
        else:
            kept_blocks = blocks
        body_text = _blocks_text(kept_blocks)
        headings = [b for b in kept_blocks if b.get("type") == "heading"]
        title_hint = headings[0]["text"] if headings else "未命名"

    # Track done steps via artifacts
    async def done(key: str) -> bool:
        async with async_session_factory() as db:
            return (await _get_art(db, sid, key)) is not None

    # ── classify ──
    if not await done("classify_v1"):
        data, meta = await call_import_agent(
            role="document_classifier",
            system_prompt=(
                "你是小说企划文档分类器。只输出 JSON。"
                "primary_type 可选: book_proposal|character_bible|world_bible|outline|"
                "writing_rules|mixed_book_proposal|unknown。"
                "sections 可为空数组。"
            ),
            user_content=f"标题提示: {title_hint}\n\n文档片段:\n{body_text[:12000]}",
        )
        async with async_session_factory() as db:
            sess = (await db.execute(select(ImportSession).where(ImportSession.id == sid))).scalar_one()
            if data:
                await _save_artifact(db, sid, artifact_type="classify", key="classify_v1", payload=data, meta=[meta])
                sess.primary_document_type = data.get("primary_type")
                sess.document_types = data.get("document_types") or [data.get("primary_type")]
                await _transition(db, sess, "analyzing", "classify")
                sess.progress = STEP_PROGRESS["classify"]
                report["steps"].append({"step": "classify", "ok": True})
            else:
                fallback = {
                    "primary_type": "mixed_book_proposal",
                    "document_types": ["mixed_book_proposal"],
                    "confidence": 0.3,
                    "sections": [],
                }
                await _save_artifact(
                    db, sid, artifact_type="classify", key="classify_v1", payload=fallback, status="degraded", meta=[meta]
                )
                await _transition(db, sess, "analyzing", "classify_degraded", {"meta": meta})
                sess.progress = STEP_PROGRESS["classify"]
                report["steps"].append({"step": "classify", "ok": False, "meta": meta})
            await db.commit()

    # ── sanitize_llm (batch on uncertain) ──
    if not await done("sanitize_llm_v1"):
        # only send review candidates
        review_blocks = []
        for b, c in zip(blocks, cand_items or [{}] * len(blocks)):
            if (c or {}).get("action") == "review" or not cand_items:
                review_blocks.append(b)
        sample = review_blocks[:40] if review_blocks else kept_blocks[:20]
        data, meta = await call_import_agent(
            role="import_sanitizer",
            system_prompt=(
                "你是企划文档清洗器。对每个 block 判断是否为 AI 对话残留/噪声。"
                "classification: source_content|assistant_chatter|user_chatter|duplicate_summary|format_noise|uncertain。"
                "action: keep|exclude|review。只输出 JSON {items:[...]}。"
            ),
            user_content=_blocks_text(sample, 10000),
        )
        async with async_session_factory() as db:
            sess = (await db.execute(select(ImportSession).where(ImportSession.id == sid))).scalar_one()
            payload = data or {"items": cand_items}
            await _save_artifact(
                db,
                sid,
                artifact_type="sanitize_llm",
                key="sanitize_llm_v1",
                payload=payload,
                status="ready" if data else "degraded",
                meta=[meta],
            )
            # rebuild kept from LLM if present
            if data and data.get("items"):
                by_id = {i.get("block_id"): i for i in data["items"]}
                new_kept = []
                for b in blocks:
                    bid = b.get("id") or b.get("block_id")
                    item = by_id.get(bid)
                    if item and item.get("action") == "exclude":
                        continue
                    new_kept.append(b)
                if new_kept:
                    await _save_artifact(
                        db,
                        sid,
                        artifact_type="document_blocks",
                        key="kept_blocks",
                        payload={"blocks": new_kept},
                    )
            await _transition(db, sess, "analyzing", "sanitize_llm")
            sess.progress = STEP_PROGRESS["sanitize_llm"]
            await db.commit()
            report["steps"].append({"step": "sanitize_llm", "ok": bool(data)})

    async with async_session_factory() as db:
        kept_art = await _get_art(db, sid, "kept_blocks")
        if kept_art and kept_art.get("blocks"):
            kept_blocks = kept_art["blocks"]
            body_text = _blocks_text(kept_blocks)

    # helper for extract steps
    async def extract_step(
        step: str,
        key: str,
        role: str,
        system: str,
        user: str,
        temp: float = 0.1,
    ) -> dict | None:
        if await done(key):
            async with async_session_factory() as db:
                return await _get_art(db, sid, key)
        data, meta = await call_import_agent(
            role=role, system_prompt=system, user_content=user, temperature=temp
        )
        async with async_session_factory() as db:
            sess = (await db.execute(select(ImportSession).where(ImportSession.id == sid))).scalar_one()
            await _save_artifact(
                db,
                sid,
                artifact_type=step,
                key=key,
                payload=data or {},
                status="ready" if data else "degraded",
                meta=[meta],
            )
            await _transition(db, sess, "analyzing", step)
            sess.progress = STEP_PROGRESS.get(step, sess.progress)
            if not data:
                sess.error_detail = f"{step}: {(meta or {}).get('validation_error') or (meta or {}).get('error')}"
            await db.commit()
        report["steps"].append({"step": step, "ok": bool(data), "meta_ok": (meta or {}).get("ok")})
        return data

    meta_out = await extract_step(
        "metadata",
        "metadata_v1",
        "book_metadata_extractor",
        "从企划书提取书目元数据。只输出 JSON。planned_chapters 为整数或 null。",
        f"文档:\n{body_text[:16000]}",
    )
    world_out = await extract_step(
        "world",
        "world_v1",
        "world_bible_extractor",
        (
            "提取世界观摘要、硬/软规则、地点。"
            "rules 每项必须含 rule_key, description；category?, is_hard。"
            "locations 每项必须含 name；description?, aliases?, rules?。"
            "文档里「规则：」「地点：」「X城/渊/谷」都要尽量收录。只输出 JSON。"
        ),
        f"文档:\n{body_text[:16000]}",
    )
    det_world = deterministic_world_from_text(body_text)
    world_out = merge_world(world_out, det_world)
    async with async_session_factory() as db:
        await _save_artifact(
            db,
            sid,
            artifact_type="world_merged",
            key="world_merged_v1",
            payload=world_out,
        )
        await db.commit()
    char_out = await extract_step(
        "characters",
        "characters_v1",
        "character_extractor",
        "提取人物列表。每项: temp_id(如 temp-char-001), canonical_name, aliases[], role, description, gender?。只输出 JSON。",
        f"文档:\n{body_text[:16000]}",
    )
    # merge characters
    characters_raw = (char_out or {}).get("characters") or []
    characters, char_conflicts = resolve_characters(characters_raw)
    async with async_session_factory() as db:
        await _save_artifact(
            db,
            sid,
            artifact_type="characters_resolved",
            key="characters_resolved_v1",
            payload={"characters": characters, "merge_conflicts": char_conflicts},
        )
        await db.commit()

    char_brief = json.dumps(
        [{"temp_id": c.get("temp_id"), "name": c.get("canonical_name")} for c in characters[:80]],
        ensure_ascii=False,
    )
    rel_out = await extract_step(
        "relationships",
        "relationships_v1",
        "relationship_extractor",
        "基于人物列表提取关系。from_temp_id/to_temp_id 必须引用已有 temp_id。只输出 JSON。",
        f"人物:\n{char_brief}\n\n文档:\n{body_text[:12000]}",
    )
    outline_out = await extract_step(
        "outline",
        "outline_v1",
        "outline_extractor_v2",
        (
            "提取卷纲与显式章纲。chapters 只收录文档中明确写出编号的章节，禁止臆造。"
            "volumes 可含 chapter_from/to。declared_total_chapters 为全文宣称总章数。"
            "只输出 JSON。"
        ),
        f"文档:\n{body_text[:20000]}",
        0.05,
    )
    # merge deterministic regex fallback (第N章 / 第N卷)
    det_outline = deterministic_outline_from_text(body_text)
    outline = merge_outline(outline_out, det_outline)
    async with async_session_factory() as db:
        await _save_artifact(
            db,
            sid,
            artifact_type="outline_merged",
            key="outline_merged_v1",
            payload=outline,
        )
        await db.commit()
    outline_out = outline
    plots_out = await extract_step(
        "plots",
        "plots_v1",
        "plot_thread_extractor",
        "提取主线/支线/伏笔。threads 含 temp_id,name,description,status,plant_chapter?。只输出 JSON。",
        f"文档:\n{body_text[:12000]}",
    )
    rules_out = await extract_step(
        "writing_rules",
        "writing_rules_v1",
        "writing_rule_extractor",
        "提取写作约束/风格要求/禁止事项。rules 项: constraint_type,title,body,is_hard,scope_type,priority。只输出 JSON。",
        f"文档:\n{body_text[:12000]}",
    )

    # conflicts
    outline = outline_out or {}
    o_conflicts = detect_outline_conflicts(
        outline.get("volumes") or [],
        outline.get("chapters") or [],
        outline.get("declared_total_chapters") or (meta_out or {}).get("planned_chapters"),
    )
    all_conflicts = list(char_conflicts) + list(o_conflicts)

    # optional LLM audit — force auditor issues to warning unless known hard codes
    HARD_AUDIT_CODES = {"CHAPTER_NO_DUPLICATE", "chapter_no_duplicate"}
    audit_payload = {
        "characters": characters[:30],
        "volumes": (outline.get("volumes") or [])[:20],
        "chapters_count": len(outline.get("chapters") or []),
        "declared_total": outline.get("declared_total_chapters"),
        "det_conflicts": all_conflicts,
    }
    audit_out = await extract_step(
        "audit",
        "audit_v1",
        "import_consistency_auditor",
        "审查导入结果一致性。输出 issues 列表与 ok 布尔。不要重复已列出的 det_conflicts 除非加重。只输出 JSON。",
        json.dumps(audit_payload, ensure_ascii=False)[:12000],
        0.0,
    )
    for iss in (audit_out or {}).get("issues") or []:
        code = str(iss.get("code") or "")
        if code not in HARD_AUDIT_CODES and iss.get("severity") == "blocking":
            iss = dict(iss)
            iss["severity"] = "warning"
            iss["message"] = (iss.get("message") or "") + "（审计建议，已降为 warning）"
        all_conflicts.append(iss)

    # classify art
    async with async_session_factory() as db:
        classify = await _get_art(db, sid, "classify_v1") or {}
        writing_rules = (rules_out or {}).get("rules") or []
        relationships = (rel_out or {}).get("relationships") or []
        preview = build_preview_bundle(
            metadata=meta_out,
            world=world_out,
            characters=characters,
            relationships=relationships,
            outline=outline,
            plots=plots_out,
            writing_rules=writing_rules,
            conflicts=all_conflicts,
            classify=classify,
        )
        preview_hash = sha256_bytes(json.dumps(preview, ensure_ascii=False, sort_keys=True).encode())

        sess = (await db.execute(select(ImportSession).where(ImportSession.id == sid))).scalar_one()
        # replace open pipeline conflicts (keep user-resolved)
        existing = (
            await db.execute(
                select(ImportConflict).where(ImportConflict.import_session_id == sid)
            )
        ).scalars().all()
        for c in existing:
            if c.status == "open" and c.code not in ("CHATTER_CANDIDATES",):
                await db.delete(c)
        for conf in all_conflicts:
            db.add(
                ImportConflict(
                    id=gen_uuid(),
                    import_session_id=sid,
                    code=conf.get("code") or "IMPORT_ISSUE",
                    severity=conf.get("severity") or "warning",
                    entity_type=conf.get("entity_type"),
                    entity_temp_id=conf.get("entity_temp_id"),
                    message=conf.get("message") or "",
                    options=conf.get("options") or [{"id": "ack", "label": "已知悉"}],
                    status="open",
                )
            )
        await _save_artifact(db, sid, artifact_type="preview", key="preview", payload=preview)
        sess.preview_hash = preview_hash
        sess.progress = 1.0
        blocking = any(c.get("severity") == "blocking" for c in all_conflicts)
        if blocking:
            await _transition(db, sess, "needs_human", "preview_ready_blocking")
        else:
            await _transition(db, sess, "preview_ready", "preview")
        sess.error_code = None
        await db.commit()
        report["status"] = sess.status
        report["preview_hash"] = preview_hash
        report["counts"] = preview.get("counts")
    return report


async def atomic_commit_import(
    db: AsyncSession,
    sess: ImportSession,
    *,
    expected_preview_hash: str,
    book_overrides: dict | None = None,
) -> dict:
    """Create Book + multi-entity rows in one transaction. No soft-pass."""
    if sess.status not in ("preview_ready", "needs_human"):
        raise ValueError(f"cannot commit from status={sess.status}")
    if expected_preview_hash != sess.preview_hash:
        raise ValueError("PREVIEW_STALE")
    blocking = (
        await db.execute(
            select(ImportConflict).where(
                ImportConflict.import_session_id == sess.id,
                ImportConflict.severity == "blocking",
                ImportConflict.status == "open",
            )
        )
    ).scalars().all()
    if blocking:
        raise ValueError(f"BLOCKING_CONFLICTS:{len(blocking)}")
    if sess.book_id:
        return {"book_id": str(sess.book_id), "status": "already_committed"}

    await _transition(db, sess, "committing", "committing")
    preview = await _get_art(db, sess.id, "preview") or {}
    meta = preview.get("metadata") or {}
    overrides = book_overrides or {}
    title = overrides.get("title") or meta.get("title") or preview.get("title_guess") or "未命名小说"
    planned = overrides.get("planned_chapters") or meta.get("planned_chapters") or preview.get("declared_total")
    if not planned:
        planned = len(preview.get("chapters") or []) or 500

    book = Book(
        id=gen_uuid(),
        title=title,
        subtitle=overrides.get("subtitle") or meta.get("subtitle"),
        description=overrides.get("description") or meta.get("synopsis"),
        status="created",
        target_chapters=int(planned),
        planned_chapters=int(planned),
        lifecycle_status="draft",
        source_import_session_id=sess.id,
        tags=overrides.get("tags") or meta.get("tags") or [],
        logline=overrides.get("logline") or meta.get("logline"),
        synopsis=meta.get("synopsis"),
        genre=overrides.get("genre") or meta.get("genre"),
        tone_summary=meta.get("tone"),
        last_activity_at=_utcnow(),
    )
    db.add(book)
    await db.flush()

    db.add(
        BookProfile(
            id=gen_uuid(),
            book_id=book.id,
            logline=book.logline,
            synopsis=meta.get("synopsis"),
            genre=book.genre,
            themes=meta.get("themes") or [],
            tone=meta.get("tone"),
            core_loop=meta.get("core_loop"),
            extra={"import_session_id": str(sess.id)},
        )
    )

    # world rules + locations
    world = preview.get("world") or {}
    for r in world.get("rules") or []:
        db.add(
            WorldRule(
                id=gen_uuid(),
                book_id=book.id,
                rule_key=(r.get("rule_key") or "rule")[:200],
                description=r.get("description") or "",
                rule_json={
                    "category": r.get("category"),
                    "is_hard": r.get("is_hard", True),
                    "source": "import_v8",
                },
            )
        )
    for loc in world.get("locations") or []:
        name = (loc.get("name") or "").strip()
        if not name:
            continue
        db.add(
            LocationCard(
                id=gen_uuid(),
                book_id=book.id,
                name=name[:300],
                aliases=loc.get("aliases") or [],
                description=loc.get("description"),
                rules=loc.get("rules") or [],
                source_refs=[{"import_session_id": str(sess.id)}],
                status="active",
            )
        )

    # characters + map temp_id
    temp_to_id: dict[str, uuid.UUID] = {}
    for c in preview.get("characters") or []:
        cid = gen_uuid()
        temp_to_id[c.get("temp_id") or c.get("canonical_name") or str(cid)] = cid
        db.add(
            CharacterCard(
                id=cid,
                book_id=book.id,
                name=(c.get("canonical_name") or "未命名")[:200],
                role=c.get("role"),
                description=c.get("description"),
                card_json={
                    "aliases": c.get("aliases") or [],
                    "gender": c.get("gender"),
                    "temp_id": c.get("temp_id"),
                    "source": "import_v8",
                },
            )
        )

    for rel in preview.get("relationships") or []:
        fa = temp_to_id.get(rel.get("from_temp_id") or "")
        tb = temp_to_id.get(rel.get("to_temp_id") or "")
        if not fa or not tb:
            continue
        db.add(
            CharacterRelationship(
                id=gen_uuid(),
                book_id=book.id,
                from_character_id=fa,
                to_character_id=tb,
                relation_type=(rel.get("relation_type") or "related")[:100],
                stage=rel.get("stage"),
                description=rel.get("description"),
                source_refs=[{"import_session_id": str(sess.id)}],
            )
        )

    # outline version + volumes + nodes
    chapters = preview.get("chapters") or []
    volumes = preview.get("volumes") or []
    if chapters or volumes:
        ov = OutlineVersion(
            id=gen_uuid(),
            book_id=book.id,
            version=1,
            status="active",
            source="import_v8",
            raw_outline=None,
            parsed_json={"volumes": volumes, "chapters": chapters},
        )
        db.add(ov)
        await db.flush()
        for v in volumes:
            db.add(
                OutlineVolume(
                    id=gen_uuid(),
                    book_id=book.id,
                    outline_version_id=ov.id,
                    volume_no=int(v.get("volume_no") or 1),
                    title=v.get("title"),
                    chapter_from=v.get("chapter_from"),
                    chapter_to=v.get("chapter_to"),
                    goal=v.get("goal"),
                    themes=v.get("themes") or [],
                    source_refs=[{"import_session_id": str(sess.id)}],
                )
            )
        # dedupe chapter_no keep first
        seen = set()
        for ch in sorted(chapters, key=lambda x: int(x.get("chapter_no") or 0)):
            n = int(ch.get("chapter_no") or 0)
            if n <= 0 or n in seen:
                continue
            seen.add(n)
            db.add(
                OutlineNode(
                    id=gen_uuid(),
                    book_id=book.id,
                    outline_version_id=ov.id,
                    node_type="chapter",
                    volume_no=int(ch.get("volume_no") or 1),
                    chapter_no=n,
                    title=ch.get("title"),
                    goal=ch.get("goal") or ch.get("title") or f"第{n}章",
                    required_beats=ch.get("required_beats") or [],
                    forbidden_outcomes=ch.get("forbidden_outcomes") or [],
                    source_refs=[{"import_session_id": str(sess.id), "heading": ch.get("source_heading")}],
                )
            )

    for t in preview.get("plot_threads") or []:
        db.add(
            PlotThread(
                id=gen_uuid(),
                book_id=book.id,
                name=(t.get("name") or "未命名线")[:200],
                description=t.get("description"),
                status=t.get("status") or "open",
                planted_chapter=t.get("plant_chapter"),
            )
        )

    for r in preview.get("writing_rules") or []:
        db.add(
            WritingConstraint(
                id=gen_uuid(),
                book_id=book.id,
                scope_type=r.get("scope_type") or "book",
                constraint_type=(r.get("constraint_type") or "style")[:100],
                title=r.get("title"),
                body=r.get("body") or "",
                priority=int(r.get("priority") or 50),
                is_hard=bool(r.get("is_hard")),
                source_refs=[{"import_session_id": str(sess.id)}],
            )
        )

    # link source
    source = (
        await db.execute(select(BookSource).where(BookSource.id == sess.source_id))
    ).scalar_one_or_none()
    if source:
        source.book_id = book.id

    sess.book_id = book.id
    await _transition(db, sess, "completed", "completed")
    sess.progress = 1.0
    sess.completed_at = _utcnow()
    return {
        "book_id": str(book.id),
        "status": "completed",
        "import_session_id": str(sess.id),
        "counts": preview.get("counts") or {},
    }


async def enqueue_import_pipeline(session_id: str) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings
    import os
    import redis.asyncio.connection as _rc

    _rc.AbstractConnection.lib_name = None
    _rc.AbstractConnection.lib_version = None
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    parts = redis_url.replace("redis://", "").split(":")
    host = parts[0]
    port = int(parts[1].split("/")[0]) if len(parts) > 1 else 6379
    pool = await create_pool(RedisSettings(host=host, port=port))
    try:
        await pool.enqueue_job(
            "run_import_pipeline_job",
            session_id,
            _job_id=f"import:{session_id}",
        )
    finally:
        await pool.close()
