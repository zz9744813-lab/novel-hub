"""v9.3 PR-07/PR-08: Quality metrics aggregation tests."""
from __future__ import annotations

import asyncio
import uuid

from app.editorial.metrics import book_quality_metrics
from app.models.tables import (
    Chapter,
    EditorialAnnotation,
    EditorialExperienceCard,
    EditorialFeedbackInsight,
    EditorialReviewRound,
)

BOOK_ID = uuid.uuid4()


class FakeResult:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return self

    def all(self):
        if isinstance(self._row, list):
            return self._row
        return [self._row] if self._row is not None else []

    def __iter__(self):
        return iter(self.all())


class FakeSession:
    def __init__(self, store: dict):
        self.store = store

    async def execute(self, stmt):
        cols = getattr(stmt, "column_descriptions", [])
        entity = cols[0].get("entity") if cols else None
        by_entity = {
            EditorialReviewRound: self.store["rounds"],
            Chapter: self.store["chapters"],
            EditorialAnnotation: self.store["annotations"],
            EditorialFeedbackInsight: self.store["insights"],
            EditorialExperienceCard: self.store["cards"],
        }
        return FakeResult(by_entity.get(entity, []))


def _ch(no: int, status="accepted"):
    return Chapter(
        id=uuid.uuid4(), book_id=BOOK_ID, chapter_no=no, outline_node_id=uuid.uuid4(),
        status="finalized", editorial_status=status, title=f"ch{no}",
    )


def _rnd(ch, no=1, verdict="accept", score=90, grade="A", dispositions=None):
    return EditorialReviewRound(
        id=uuid.uuid4(), book_id=BOOK_ID, chapter_id=ch.id, chapter_version_id=uuid.uuid4(),
        round_no=no, status="submitted", verdict=verdict, score_total=score, grade=grade,
        rubric_scores_json=None, overall_comment=None, reviewer_kind="human", reviewer_id=None,
        ai_issue_dispositions=dispositions or {}, submitted_at=None, completed_at=None,
    )


def _ann(rnd, sev="major", cat="dialogue", matched=False, typ="issue"):
    return EditorialAnnotation(
        id=uuid.uuid4(), review_round_id=rnd.id, book_id=BOOK_ID, chapter_id=rnd.chapter_id,
        chapter_version_id=rnd.chapter_version_id, annotation_type=typ, category=cat,
        severity=sev, scope="local_span", scene_no=None, paragraph_key="0",
        start_offset=None, end_offset=None, quoted_text="q", quote_hash=None,
        context_before=None, context_after=None, context_hash=None, comment=None,
        suggested_text=None, is_blocking=False,
        ai_issue_match_ids=[str(uuid.uuid4())] if matched else [],
        tags=[], resolution_status="open", resolved_by_version_id=None,
    )


def _insight(ann, category, component):
    return EditorialFeedbackInsight(
        id=uuid.uuid4(), annotation_id=ann.id, book_id=BOOK_ID,
        normalized_category=category, human_intent=None, symptom=None,
        root_cause_component=component, secondary_components=[], remediation_level="L1",
        confidence=0.7, evidence_refs=[],
    )


def _run(store):
    return asyncio.run(book_quality_metrics(FakeSession(store), BOOK_ID))


def _base_store():
    return {"rounds": [], "chapters": [], "annotations": [], "insights": [], "cards": []}


class TestFirstPassYield:
    def test_yield_counts_round1_accepts_only(self):
        ch1, ch2, ch3 = _ch(1), _ch(2), _ch(3)
        store = _base_store()
        store["chapters"] = [ch1, ch2, ch3]
        store["rounds"] = [
            _rnd(ch1, no=1, verdict="accept"),  # first pass ✓
            _rnd(ch2, no=1, verdict="revise", score=60, grade="D"),
            _rnd(ch2, no=2, verdict="accept"),  # needed 2 rounds ✗
            _rnd(ch3, no=1, verdict="accept_with_notes"),  # ✓
        ]
        m = _run(store)
        assert m["total_reviewed"] == 3
        assert m["first_pass_accepted"] == 2
        assert m["first_pass_yield"] == 66.7
        assert m["revision_depth"] == {"1": 2, "2": 1}

    def test_empty_book_returns_nulls(self):
        m = _run(_base_store())
        assert m["first_pass_yield"] is None
        assert m["total_reviewed"] == 0
        assert m["consecutive_bad"] == 0


class TestParetoAndRootCause:
    def test_pareto_and_roots_sorted_desc(self):
        ch = _ch(1)
        r = _rnd(ch)
        a1, a2, a3 = _ann(r, cat="dialogue"), _ann(r, cat="dialogue"), _ann(r, cat="style")
        store = _base_store()
        store["chapters"] = [ch]
        store["rounds"] = [r]
        store["annotations"] = [a1, a2, a3]
        store["insights"] = [
            _insight(a1, "dialogue", "voice"),
            _insight(a2, "dialogue", "voice"),
            _insight(a3, "style", "style"),
        ]
        m = _run(store)
        assert list(m["category_pareto"]) == ["dialogue", "style"]
        assert m["category_pareto"]["dialogue"] == 4  # 2 insights + 2 annotations
        assert m["root_causes"]["voice"] == 2

    def test_praise_and_question_excluded_from_pareto(self):
        ch = _ch(1)
        r = _rnd(ch)
        store = _base_store()
        store["chapters"] = [ch]
        store["rounds"] = [r]
        store["annotations"] = [
            _ann(r, typ="praise", sev="praise", cat="dialogue"),
            _ann(r, typ="question", cat="dialogue"),
            _ann(r, cat="plot"),
        ]
        m = _run(store)
        assert m["category_pareto"] == {"plot": 1}


class TestAiCalibration:
    def test_agreement_and_escape_rate(self):
        ch = _ch(1)
        r1 = _rnd(ch, dispositions={
            str(uuid.uuid4()): "confirmed",
            str(uuid.uuid4()): "confirmed",
            str(uuid.uuid4()): "dismissed",
        })
        store = _base_store()
        store["chapters"] = [ch]
        store["rounds"] = [r1]
        store["annotations"] = [
            _ann(r1, sev="critical", matched=True),   # caught by AI
            _ann(r1, sev="major", matched=False),     # escaped
            _ann(r1, sev="minor", matched=False),     # not severe → ignored
            _ann(r1, sev="major", typ="praise", matched=False),  # praise → ignored
        ]
        m = _run(store)
        cal = m["ai_calibration"]
        assert cal["confirmed"] == 2
        assert cal["dismissed"] == 1
        assert cal["agreement"] == 66.7
        assert cal["severe_human_issues"] == 2
        assert cal["escaped"] == 1
        assert cal["escape_rate"] == 50.0


class TestSignals:
    def test_consecutive_bad_and_window_good_rate(self):
        chs = [_ch(i) for i in range(1, 6)]
        store = _base_store()
        store["chapters"] = chs
        store["rounds"] = [
            _rnd(chs[0], verdict="accept", score=92, grade="A"),
            _rnd(chs[1], verdict="accept", score=88, grade="B"),
            _rnd(chs[2], verdict="revise", score=58, grade="D"),
            _rnd(chs[3], verdict="revise", score=62, grade="D"),
            _rnd(chs[4], verdict="revise", score=70, grade="C"),
        ]
        m = _run(store)
        assert m["consecutive_bad"] == 3
        assert m["window_good_rate"] == 40.0  # 2/5 ≥85

    def test_status_distribution_and_cards(self):
        store = _base_store()
        store["chapters"] = [_ch(1, "accepted"), _ch(2, "pending_review")]
        store["cards"] = [
            type("C", (), {"status": "active"})(),
            type("C", (), {"status": "active"})(),
            type("C", (), {"status": "candidate"})(),
        ]
        m = _run(store)
        assert m["status_distribution"] == {"accepted": 1, "pending_review": 1}
        assert m["experience_cards"]["active"] == 2
        assert m["experience_cards"]["candidate"] == 1
