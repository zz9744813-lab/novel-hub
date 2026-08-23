"""v9.3 PR-03/PR-05: Editorial worker jobs tests (revision ladder + analysis).

Fake session replaces PostgreSQL; LLM rewrite monkeypatched to None (offline
deterministic path). Verifies the L0..L5 ladder state machine, version
lineage, annotation re-anchoring and the deterministic feedback analyst.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

import app.editorial.jobs as jobs
from app.models.tables import (
    Chapter,
    ChapterVersion,
    EditorialAnnotation,
    EditorialFeedbackInsight,
    EditorialImprovementProposal,
    EditorialPreferencePair,
    EditorialRegressionCase,
    EditorialReviewRound,
)

BOOK_ID = uuid.uuid4()

CONTENT = (
    "沈砚把玉佩按在桌上，烛火晃了一下。\n\n"
    "“你早就知道。”他说，声音很平，没有任何情绪。\n\n"
    "陆晚没有否认。窗外渡口的方向传来梆子声，三更了。"
)


class FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        if isinstance(self._row, list):
            return self._row
        return [self._row] if self._row is not None else []

    def __iter__(self):
        return iter(self.all())


def _where_vals(stmt) -> dict:
    out: dict = {}
    wc = getattr(stmt, "whereclause", None)
    if wc is None:
        return out
    for crit in list(getattr(wc, "clauses", None) or [wc]):
        name = getattr(getattr(crit, "left", None), "name", None)
        if not name:
            continue
        out[name] = getattr(getattr(crit, "right", None), "value", None)
    return out


def _is_desc(stmt) -> bool:
    for ob in getattr(stmt, "_order_by_clauses", None) or []:
        if "DESC" in str(ob).upper():
            return True
    return False


class FakeSession:
    """In-memory session covering every query the two jobs issue."""

    def __init__(self, store: dict):
        self.store = store
        self.commits = 0

    async def execute(self, stmt):
        vals = _where_vals(stmt)
        cols = getattr(stmt, "column_descriptions", [])
        entity = cols[0].get("entity") if cols else None

        if entity is EditorialReviewRound:
            if "id" in vals:
                for r in self.store["rounds"]:
                    if str(r.id) == str(vals["id"]):
                        return FakeResult(r)
                return FakeResult(None)
            matched = [r for r in self.store["rounds"] if str(r.chapter_id) == str(vals.get("chapter_id"))]
            return FakeResult(sorted(matched, key=lambda r: r.round_no))
        if entity is Chapter:
            for ch in self.store["chapters"]:
                if str(ch.id) == str(vals.get("id")):
                    return FakeResult(ch)
            return FakeResult(None)
        if entity is ChapterVersion:
            if "id" in vals:
                for v in self.store["versions"]:
                    if str(v.id) == str(vals["id"]):
                        return FakeResult(v)
                return FakeResult(None)
            matched = [v for v in self.store["versions"] if str(v.chapter_id) == str(vals.get("chapter_id"))]
            matched.sort(key=lambda v: v.version, reverse=True)
            if _is_desc(stmt):
                return FakeResult(matched[0] if matched else None)
            return FakeResult(matched)
        if entity is EditorialAnnotation:
            matched = [a for a in self.store["annotations"] if str(a.review_round_id) == str(vals.get("review_round_id"))]
            return FakeResult(matched)
        if entity is EditorialFeedbackInsight:
            matched = [i for i in self.store.get("insights", []) if str(i.annotation_id) == str(vals.get("annotation_id"))]
            return FakeResult(matched[0] if matched else None)
        if entity is EditorialPreferencePair:
            matched = [p for p in self.store.get("pairs", []) if str(p.annotation_id) == str(vals.get("annotation_id"))]
            return FakeResult(matched[0] if matched else None)
        if entity is EditorialRegressionCase:
            matched = [c for c in self.store.get("cases", []) if str(c.source_review_round_id) == str(vals.get("source_review_round_id"))]
            return FakeResult(matched[0] if matched else None)
        return FakeResult(None)

    def add(self, obj):
        buckets = {
            ChapterVersion: "versions",
            EditorialFeedbackInsight: "insights",
            EditorialPreferencePair: "pairs",
            EditorialRegressionCase: "cases",
            EditorialImprovementProposal: "proposals",
        }
        for cls, key in buckets.items():
            if isinstance(obj, cls):
                self.store.setdefault(key, []).append(obj)
                return

    async def flush(self):
        for key in ("versions", "insights", "pairs", "cases", "proposals"):
            for obj in self.store.get(key, []):
                if getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _store(chapter_no: int = 31):
    ch = Chapter(
        id=uuid.uuid4(),
        book_id=BOOK_ID,
        chapter_no=chapter_no,
        outline_node_id=uuid.uuid4(),
        status="finalized",
        editorial_status="revising",
        title=f"第{chapter_no}章",
    )
    ver = ChapterVersion(
        id=uuid.uuid4(),
        book_id=BOOK_ID,
        chapter_id=ch.id,
        version=3,
        content=CONTENT,
        word_count=len(CONTENT),
        source_run_id=uuid.uuid4(),
        version_kind="final",
    )
    rnd = EditorialReviewRound(
        id=uuid.uuid4(),
        book_id=BOOK_ID,
        chapter_id=ch.id,
        chapter_version_id=ver.id,
        round_no=1,
        status="submitted",
        verdict="revise",
        score_total=62,
        grade="D",
        rubric_scores_json=None,
        overall_comment="对话太干",
        reviewer_kind="human",
        reviewer_id=None,
        ai_issue_dispositions={},
    )
    return {"chapters": [ch], "versions": [ver], "rounds": [rnd], "annotations": []}, ch, ver, rnd


def _annotation(rnd, ver, **kw):
    paras = CONTENT.split("\n\n")
    class _Stub:
        id = uuid.uuid4()
    rnd = rnd or _Stub()
    ver = ver or _Stub()
    base = dict(
        id=uuid.uuid4(),
        review_round_id=rnd.id,
        book_id=BOOK_ID,
        chapter_id=getattr(rnd, "chapter_id", None),
        chapter_version_id=ver.id,
        annotation_type="direct_edit",
        category="dialogue",
        severity="major",
        scope="local_span",
        scene_no=None,
        paragraph_key="1",
        start_offset=1,
        end_offset=1 + len("你早就知道。"),
        quoted_text="你早就知道。",
        quote_hash=None,
        context_before="",
        context_after="",
        context_hash=None,
        comment="去掉说明性尾句",
        suggested_text="你早就知道。",
        is_blocking=False,
        ai_issue_match_ids=[],
        tags=[],
        resolution_status="open",
        resolved_by_version_id=None,
    )
    base.update(kw)
    return EditorialAnnotation(**base)


# ── pure helpers ───────────────────────────────────────────────────────


class TestApplyDirectEdits:
    def test_applies_matching_edit(self):
        paras = CONTENT.split("\n\n")
        ann = _annotation(None, None, quoted_text="声音很平，没有任何情绪", suggested_text="声音哑了一下")
        out, n, ids = jobs.apply_direct_edits(paras, [ann])
        assert n == 1
        assert ids == {ann.id}
        assert "声音哑了一下" in out[1]
        assert "没有任何情绪" not in out[1]
        assert out[0] == paras[0]  # untouched

    def test_multiple_edits_same_paragraph_keep_offsets(self):
        paras = ["甲乙丙丁戊己庚辛。"]
        a1 = _annotation(None, None, paragraph_key="0", quoted_text="乙", suggested_text="北")
        a2 = _annotation(None, None, paragraph_key="0", quoted_text="庚", suggested_text="唐")
        out, n, ids = jobs.apply_direct_edits(paras, [a1, a2])
        assert n == 2
        assert ids == {a1.id, a2.id}
        assert out[0] == "甲北丙丁戊己唐辛。"

    def test_skips_unmatched_quote(self):
        paras = CONTENT.split("\n\n")
        ann = _annotation(None, None, quoted_text="不存在的文本", suggested_text="x")
        out, n, ids = jobs.apply_direct_edits(paras, [ann])
        assert n == 0
        assert ids == set()
        assert "\n\n".join(out) == CONTENT


class TestBuildRevisionInstructions:
    def test_excludes_praise_and_numbers_items(self):
        issue = _annotation(
            None, None, annotation_type="issue", quoted_text="梆子声", comment="三更不该有梆子", suggested_text=None
        )
        praise = _annotation(None, None, annotation_type="praise", severity="praise", comment="好")
        text = jobs.build_revision_instructions([issue, praise], "L1")
        assert "三更不该有梆子" in text
        assert "1." in text
        assert "好" not in text


# ── revision ladder ────────────────────────────────────────────────────


def _raise():
    raise AssertionError("LLM must not be called on this path")


def _run_ladder(store, review_id, level):
    def _factory():
        return FakeSession(store)

    orig = jobs.async_session_factory
    jobs.async_session_factory = _factory
    try:
        return asyncio.run(jobs.run_editorial_revision_job(None, str(review_id), level))
    finally:
        jobs.async_session_factory = orig


class TestRevisionLadder:
    def test_l0_learning_only_waives_without_new_version(self, monkeypatch):
        store, ch, ver, rnd = _store()
        monkeypatch.setattr(jobs, "_llm_rewrite", lambda *a, **k: _raise())
        result = _run_ladder(store, rnd.id, "L0")
        assert result["status"] == "waived_learning_only"
        assert ch.editorial_status == "waived"
        assert len(store["versions"]) == 1

    def test_l5_creates_improvement_proposal(self, monkeypatch):
        store, ch, ver, rnd = _store()
        store["annotations"].append(_annotation(rnd, ver, annotation_type="issue", suggested_text=None))
        monkeypatch.setattr(jobs, "_llm_rewrite", lambda *a, **k: _raise())
        result = _run_ladder(store, rnd.id, "L5")
        assert result["status"] == "proposal_created"
        assert len(store.get("proposals", [])) == 1
        assert store["proposals"][0].proposal_type == "editorial_feedback_batch"
        assert ch.editorial_status == "waived"

    def test_l1_patches_direct_edit_and_creates_lineage_version(self, monkeypatch):
        store, ch, ver, rnd = _store()
        ann = _annotation(
            rnd, ver, quoted_text="声音很平，没有任何情绪", suggested_text="声音哑了一下", start_offset=9
        )
        store["annotations"].append(ann)
        monkeypatch.setattr(jobs, "_llm_rewrite", lambda *a, **k: _raise())
        result = _run_ladder(store, rnd.id, "L1")
        assert result["status"] == "revised"
        assert ch.editorial_status == "awaiting_recheck"

        new_v = store["versions"][-1]
        assert new_v.version == 4
        assert new_v.parent_version_id == ver.id
        assert new_v.editorial_review_round_id == rnd.id
        assert new_v.revision_origin == "editorial_revision"
        assert "声音哑了一下" in new_v.content
        assert "没有任何情绪" not in new_v.content
        # direct edit annotation re-anchored & resolved on the new text
        assert ann.resolution_status == "resolved"
        assert ann.resolved_by_version_id == new_v.id

    def test_l2_without_llm_falls_back_to_deterministic_patch(self, monkeypatch):
        store, ch, ver, rnd = _store()
        store["annotations"].append(
            _annotation(rnd, ver, quoted_text="声音很平，没有任何情绪", suggested_text="声音哑了一下")
        )
        async def _none(content, instructions):
            return None

        monkeypatch.setattr(jobs, "_llm_rewrite", _none)
        result = _run_ladder(store, rnd.id, "L2")
        assert result["status"] == "fallback_patched"
        assert store["versions"][-1].revision_origin == "editorial_revision_fallback"
        assert ch.editorial_status == "awaiting_recheck"

    def test_l2_with_llm_rewrites_and_replans_l4_origin(self, monkeypatch):
        store, ch, ver, rnd = _store()

        async def _rewrite(content, instructions):
            return "重写后的整章正文。\n\n第二段。"

        monkeypatch.setattr(jobs, "_llm_rewrite", _rewrite)
        result = _run_ladder(store, rnd.id, "L4")
        assert result["status"] == "revised"
        assert store["versions"][-1].revision_origin == "editorial_replan"
        assert store["versions"][-1].content.startswith("重写后的整章正文")

    def test_round_not_found_is_safe(self):
        store, *_ = _store()
        result = _run_ladder(store, uuid.uuid4(), "L1")
        assert result["status"] == "not_found"


def _raise():
    raise AssertionError("LLM must not be called on this path")


def _run_ladder(store, review_id, level):
    def _factory():
        return FakeSession(store)

    orig = jobs.async_session_factory
    jobs.async_session_factory = _factory
    try:
        return asyncio.run(jobs.run_editorial_revision_job(None, str(review_id), level))
    finally:
        jobs.async_session_factory = orig


# ── feedback analyst ───────────────────────────────────────────────────


class TestFeedbackAnalyst:
    def _run_analysis(self, store, review_id):
        def _factory():
            return FakeSession(store)

        orig = jobs.async_session_factory
        jobs.async_session_factory = _factory
        try:
            return asyncio.run(jobs.analyze_editorial_review_job(None, str(review_id)))
        finally:
            jobs.async_session_factory = orig

    def test_attributes_root_cause_and_creates_preference_pair(self):
        store, ch, ver, rnd = _store()
        store["annotations"].append(_annotation(rnd, ver))  # direct_edit, category=dialogue
        store["annotations"].append(
            _annotation(
                rnd, ver,
                annotation_type="issue",
                category="causality",
                suggested_text=None,
                comment="转折缺乏因果",
            )
        )
        result = self._run_analysis(store, rnd.id)
        assert result["status"] == "ok"
        assert result["insights"] == 2
        assert result["preference_pairs"] == 1

        by_cat = {i.normalized_category: i for i in store["insights"]}
        assert by_cat["dialogue"].root_cause_component == "voice"
        assert by_cat["causality"].root_cause_component == "ccne"

        pair = store["pairs"][0]
        assert pair.rejected_text == "你早就知道。"
        assert pair.chosen_text == "你早就知道。"
        assert pair.source == "human_direct_edit"

        # regression snapshot persisted with verdict
        case = store["cases"][0]
        assert case.human_verdict == "revise"
        assert len(case.human_annotation_ids) == 2

    def test_idempotent_second_run_adds_nothing(self):
        store, ch, ver, rnd = _store()
        store["annotations"].append(_annotation(rnd, ver))
        self._run_analysis(store, rnd.id)
        first = (len(store["insights"]), len(store["pairs"]), len(store["cases"]))
        result = self._run_analysis(store, rnd.id)
        assert result["insights"] == 0
        assert result["preference_pairs"] == 0
        assert (len(store["insights"]), len(store["pairs"]), len(store["cases"])) == first

    def test_skips_unsubmitted_round(self):
        store, ch, ver, rnd = _store()
        rnd.status = "draft"
        result = self._run_analysis(store, rnd.id)
        assert result["status"] == "skipped"
        assert store.get("insights", []) == []

    def test_scope_hint_used_when_category_unknown(self):
        store, ch, ver, rnd = _store()
        store["annotations"].append(
            _annotation(rnd, ver, category=None, scope="character", suggested_text=None, annotation_type="issue")
        )
        self._run_analysis(store, rnd.id)
        assert store["insights"][0].root_cause_component == "voice"
        assert store["insights"][0].normalized_category == "other"
