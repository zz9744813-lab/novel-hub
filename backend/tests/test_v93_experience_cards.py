"""v9.3 PR-06: Experience cards engine tests (merge/dedupe/retrieve/inject)."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.editorial.experience import (
    render_cards_for_prompt,
    retrieve_cards,
    set_card_status,
    synthesize_cards_from_review,
)
from app.models.tables import (
    EditorialAnnotation,
    EditorialExperienceCard,
    EditorialFeedbackInsight,
    EditorialReviewRound,
)

BOOK_ID = uuid.uuid4()
OTHER_BOOK = uuid.uuid4()


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


class FakeSession:
    def __init__(self, store: dict):
        self.store = store
        self.commits = 0

    async def execute(self, stmt):
        cols = getattr(stmt, "column_descriptions", [])
        entity = cols[0].get("entity") if cols else None
        wc = getattr(stmt, "whereclause", None)
        vals: dict = {}
        if wc is not None:
            for crit in list(getattr(wc, "clauses", None) or [wc]):
                name = getattr(getattr(crit, "left", None), "name", None)
                if name:
                    vals[name] = getattr(getattr(crit, "right", None), "value", None)

        if entity is EditorialReviewRound:
            for r in self.store["rounds"]:
                if str(r.id) == str(vals.get("id")):
                    return FakeResult(r)
            return FakeResult(None)
        if entity is EditorialAnnotation:
            matched = [
                a for a in self.store["annotations"]
                if str(a.review_round_id) == str(vals.get("review_round_id"))
            ]
            return FakeResult(matched)
        if entity is EditorialFeedbackInsight:
            wanted = vals.get("annotation_id")
            if wanted is not None:
                # IN-clause: match any of the annotation ids listed
                matched = [
                    i for i in self.store.get("insights", [])
                    if str(i.annotation_id) in {str(w) for w in _flatten(wanted)}
                ]
                return FakeResult(matched)
            return FakeResult(list(self.store.get("insights", [])))
        if entity is EditorialExperienceCard:
            status_filter = vals.get("status")
            if "id" in vals:
                for c in self.store.get("cards", []):
                    if str(c.id) == str(vals["id"]):
                        return FakeResult(c)
                return FakeResult(None)
            matched = list(self.store.get("cards", []))
            if status_filter is not None:
                allowed = {str(s) for s in _flatten(status_filter)}
                matched = [c for c in matched if c.status in allowed]
            return FakeResult(matched)
        return FakeResult(None)

    def add(self, obj):
        if isinstance(obj, EditorialExperienceCard):
            self.store.setdefault("cards", []).append(obj)

    async def flush(self):
        for c in self.store.get("cards", []):
            if getattr(c, "id", None) is None:
                c.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _flatten(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple, set)):
        return list(v)
    return [v]


def _round(book_id=BOOK_ID):
    return EditorialReviewRound(
        id=uuid.uuid4(),
        book_id=book_id,
        chapter_id=uuid.uuid4(),
        chapter_version_id=uuid.uuid4(),
        round_no=1,
        status="submitted",
        verdict="revise",
        score_total=70,
        grade="C",
        rubric_scores_json=None,
        overall_comment=None,
        reviewer_kind="human",
        reviewer_id=None,
        ai_issue_dispositions={},
    )


def _ann(rnd, **kw):
    base = dict(
        id=uuid.uuid4(),
        review_round_id=rnd.id,
        book_id=rnd.book_id,
        chapter_id=rnd.chapter_id,
        chapter_version_id=rnd.chapter_version_id,
        annotation_type="issue",
        category="dialogue",
        severity="major",
        scope="local_span",
        scene_no=None,
        paragraph_key="0",
        start_offset=None,
        end_offset=None,
        quoted_text="x",
        quote_hash=None,
        context_before=None,
        context_after=None,
        context_hash=None,
        comment="对白要带潜台词，不要直说",
        suggested_text=None,
        is_blocking=False,
        ai_issue_match_ids=[],
        tags=[],
        resolution_status="open",
        resolved_by_version_id=None,
    )
    base.update(kw)
    return EditorialAnnotation(**base)


def _insight(ann, component="voice"):
    return EditorialFeedbackInsight(
        id=uuid.uuid4(),
        annotation_id=ann.id,
        book_id=ann.book_id,
        normalized_category=ann.category or "other",
        human_intent=ann.comment,
        symptom=None,
        root_cause_component=component,
        secondary_components=[],
        remediation_level="L1",
        confidence=0.7,
        evidence_refs=[],
    )


def _card(book_id=BOOK_ID, **kw):
    base = dict(
        id=uuid.uuid4(),
        book_id=book_id,
        rule_type="preference",
        scope_type="book",
        scope_ref={},
        category="dialogue",
        trigger_conditions={"category": "dialogue", "component": "voice"},
        instruction="对白：对白要带潜台词，不要直说",
        rationale=None,
        avoid_when=[],
        target_components=["draft_writer"],
        positive_example_refs=[],
        negative_example_refs=[],
        support_count=1,
        contradiction_count=0,
        confidence=0.5,
        status="active",
        is_locked=False,
        effective_from_chapter=None,
        last_confirmed_at=None,
        source_annotation_ids=[],
    )
    base.update(kw)
    return EditorialExperienceCard(**base)


def _run(coro):
    return asyncio.run(coro)


class TestSynthesize:
    def test_creates_cards_from_annotations_with_insights(self):
        rnd = _round()
        anns = [_ann(rnd), _ann(rnd, annotation_type="forbidden_pattern", comment="禁止用'瞬间移动'")]
        store = {"rounds": [rnd], "annotations": anns, "insights": [_insight(anns[0])], "cards": []}
        result = _run(synthesize_cards_from_review(FakeSession(store), rnd.id))
        assert result["status"] == "ok"
        assert result["created"] == 2
        cards = store["cards"]
        types = {c.rule_type for c in cards}
        assert types == {"anti_pattern"}  # issue + forbidden_pattern → anti_pattern
        instructions = {c.instruction for c in cards}
        assert any(i.startswith("禁止出现") for i in instructions)
        assert all(c.status == "candidate" for c in cards)

    def test_similar_annotation_merges_into_existing_card(self):
        rnd = _round()
        ann = _ann(rnd)  # same comment as seeded card
        existing = _card()  # instruction identical → similarity 1.0
        store = {"rounds": [rnd], "annotations": [ann], "insights": [], "cards": [existing]}
        result = _run(synthesize_cards_from_review(FakeSession(store), rnd.id))
        assert result["created"] == 0
        assert result["merged"] == 1
        assert len(store["cards"]) == 1
        assert existing.support_count == 2
        assert str(ann.id) in existing.source_annotation_ids
        assert existing.confidence > 0.5

    def test_locked_card_not_merged(self):
        rnd = _round()
        ann = _ann(rnd)
        locked = _card(is_locked=True, status="locked")
        store = {"rounds": [rnd], "annotations": [ann], "insights": [], "cards": [locked]}
        result = _run(synthesize_cards_from_review(FakeSession(store), rnd.id))
        assert result["created"] == 1  # new card; locked one untouched
        assert locked.support_count == 1

    def test_question_annotations_skipped(self):
        rnd = _round()
        ann = _ann(rnd, annotation_type="question")
        store = {"rounds": [rnd], "annotations": [ann], "insights": [], "cards": []}
        result = _run(synthesize_cards_from_review(FakeSession(store), rnd.id))
        assert result["created"] == 0

    def test_praise_becomes_positive_pattern(self):
        rnd = _round()
        ann = _ann(rnd, annotation_type="praise", severity="praise", comment="这段留白很妙")
        store = {"rounds": [rnd], "annotations": [ann], "insights": [], "cards": []}
        _run(synthesize_cards_from_review(FakeSession(store), rnd.id))
        assert store["cards"][0].rule_type == "positive_pattern"
        assert store["cards"][0].instruction.startswith("保持优点")

    def test_round_not_found(self):
        store = {"rounds": [], "annotations": [], "insights": [], "cards": []}
        result = _run(synthesize_cards_from_review(FakeSession(store), uuid.uuid4()))
        assert result["status"] == "round_not_found"


class TestRetrieveAndInject:
    def test_scope_filtering_and_ranking(self):
        cards = [
            _card(support_count=1),  # this book, low support
            _card(support_count=9),  # this book, high support → first
            _card(book_id=OTHER_BOOK),  # other book → excluded
            _card(book_id=None, scope_type="global", support_count=3),
        ]
        store = {"cards": cards, "rounds": [], "annotations": [], "insights": []}
        got = _run(retrieve_cards(FakeSession(store), BOOK_ID, limit=10))
        ids = [c.id for c in got]
        assert cards[1].id == ids[0]
        assert cards[2].id not in ids
        assert cards[0].id in ids
        assert cards[3].id in ids

    def test_effective_from_chapter_gate(self):
        card = _card(effective_from_chapter=40)
        store = {"cards": [card], "rounds": [], "annotations": [], "insights": []}
        db = FakeSession(store)
        assert _run(retrieve_cards(db, BOOK_ID, chapter_no=39)) == []
        assert _run(retrieve_cards(db, BOOK_ID, chapter_no=40)) == [card]

    def test_include_candidates_flag(self):
        cand = _card(status="candidate")
        store = {"cards": [cand], "rounds": [], "annotations": [], "insights": []}
        db = FakeSession(store)
        assert _run(retrieve_cards(db, BOOK_ID)) == []
        assert _run(retrieve_cards(db, BOOK_ID, include_candidates=True)) == [cand]

    def test_render_block(self):
        block = render_cards_for_prompt([_card()])
        assert "<写作经验>" in block
        assert "对白要带潜台词" in block
        assert render_cards_for_prompt([]) == ""


class TestLifecycle:
    def test_activate_and_reject(self):
        card = _card(status="candidate")
        store = {"cards": [card], "rounds": [], "annotations": [], "insights": []}
        db = FakeSession(store)
        got = _run(set_card_status(db, card.id, "active"))
        assert got.status == "active"
        assert got.last_confirmed_at is not None
        got = _run(set_card_status(db, card.id, "rejected", lock=False))
        assert got.status == "rejected"

    def test_invalid_status_raises(self):
        card = _card()
        store = {"cards": [card], "rounds": [], "annotations": [], "insights": []}
        with pytest.raises(ValueError, match="INVALID_CARD_STATUS"):
            _run(set_card_status(FakeSession(store), card.id, "bogus"))

    def test_missing_card_returns_none(self):
        store = {"cards": [], "rounds": [], "annotations": [], "insights": []}
        assert _run(set_card_status(FakeSession(store), uuid.uuid4(), "active")) is None
