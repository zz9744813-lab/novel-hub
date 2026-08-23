"""v9.3 PR-02: Editorial review API tests (spec §82–§87).

Fake session replaces PostgreSQL; enqueue monkeypatched. Verifies policy
bootstrap/update, queue filters, round lifecycle (create → detail →
submit), fail-closed annotation anchoring, AI issue dispositions and
revision triggering.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.editorial as editorial_api
from app.database import get_db
from app.models.tables import (
    Book,
    Chapter,
    ChapterVersion,
    EditorialAnnotation,
    EditorialPreferencePair,
    EditorialReviewPolicy,
    EditorialReviewRound,
    EditorialRubricTemplate,
    ReviewIssue,
)

BOOK_ID = uuid.uuid4()

CONTENT = (
    "沈砚把玉佩按在桌上，烛火晃了一下。\n\n"
    "“你早就知道。”他说，声音很平。\n\n"
    "陆晚没有否认。窗外渡口的方向传来梆子声，三更了。"
)


def _book(**kw) -> Book:
    base = dict(id=BOOK_ID, title="诸天红颜录")
    base.update(kw)
    return Book(**base)


def _chapter(no: int = 31, **kw) -> Chapter:
    base = dict(
        id=uuid.uuid4(),
        book_id=BOOK_ID,
        chapter_no=no,
        outline_node_id=uuid.uuid4(),
        status="finalized",
        editorial_status="pending_review",
        title=f"第{no}章",
    )
    base.update(kw)
    return Chapter(**base)


def _version(ch: Chapter, version: int = 3, **kw) -> ChapterVersion:
    base = dict(
        id=uuid.uuid4(),
        book_id=ch.book_id,
        chapter_id=ch.id,
        version=version,
        content=CONTENT,
        word_count=len(CONTENT),
        source_run_id=uuid.uuid4(),
        version_kind="final",
    )
    base.update(kw)
    return ChapterVersion(**base)


def _issue(ch: Chapter) -> ReviewIssue:
    return ReviewIssue(
        id=uuid.uuid4(),
        book_id=ch.book_id,
        chapter_id=ch.id,
        scene_id=uuid.uuid4(),
        paragraph_id="P-031-01",
        issue_type="character_motivation",
        severity="major",
        evidence="人物动机不足",
    )


class FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalar_one(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        if isinstance(self._row, list):
            return self._row
        return [self._row] if self._row is not None else []


def _crits(stmt):
    wc = getattr(stmt, "whereclause", None)
    if wc is None:
        return []
    return list(getattr(wc, "clauses", None) or [wc])


def _where_vals(stmt) -> dict:
    out: dict = {}
    for crit in _crits(stmt):
        name = getattr(getattr(crit, "left", None), "name", None)
        if not name:
            continue
        right = getattr(crit, "right", None)
        out[name] = getattr(right, "value", None)
    return out


def _is_desc(stmt) -> bool:
    for ob in getattr(stmt, "_order_by_clauses", None) or []:
        if str(getattr(ob, "modifier", "")).endswith("DESC"):
            return True
        if "DESC" in str(ob).upper():
            return True
    return False


def _agg(stmt):
    """Detect func.count/max aggregates via compiled SQL text (robust)."""
    s = str(stmt)
    if "count(review_issues.id)" in s:
        return "count", "review_issues", None
    if "count(editorial_review_rounds.id)" in s:
        return "count", "editorial_review_rounds", None
    if "max(editorial_review_rounds.round_no)" in s:
        return "max", "editorial_review_rounds", "round_no"
    return None, None, None


class FakeSession:
    def __init__(self, store: dict):
        self.store = store
        self.commits = 0
        self.deleted: list = []

    async def execute(self, stmt):
        fn, table, col = _agg(stmt)
        vals = _where_vals(stmt)

        if fn == "count":
            if table == "review_issues":
                n = sum(1 for i in self.store["issues"] if str(i.chapter_id) == str(vals.get("chapter_id")))
                return FakeResult(n)
            if table == "editorial_review_rounds":
                n = sum(1 for r in self.store["rounds"] if str(r.chapter_id) == str(vals.get("chapter_id")))
                return FakeResult(n)
            return FakeResult(0)
        if fn == "max":
            nos = [r.round_no for r in self.store["rounds"] if str(r.chapter_id) == str(vals.get("chapter_id"))]
            return FakeResult(max(nos) if nos else None)

        cols = getattr(stmt, "column_descriptions", [])
        entity = cols[0].get("entity") if cols else None

        if entity is Chapter:
            if "id" in vals:
                for ch in self.store["chapters"]:
                    if str(ch.id) == str(vals["id"]):
                        return FakeResult(ch)
                return FakeResult(None)
            matched = self.store["chapters"]
            if vals.get("status"):
                matched = [c for c in matched if c.status == vals["status"]]
            es = vals.get("editorial_status")
            if es is not None:
                if isinstance(es, (list, tuple, set)):
                    matched = [c for c in matched if c.editorial_status in set(es)]
                else:
                    matched = [c for c in matched if c.editorial_status == es]
            if vals.get("book_id"):
                matched = [c for c in matched if str(c.book_id) == str(vals["book_id"])]
            return FakeResult(sorted(matched, key=lambda c: c.chapter_no))

        if entity is ChapterVersion:
            if "id" in vals:
                for v in self.store["versions"]:
                    if str(v.id) == str(vals["id"]):
                        return FakeResult(v)
                return FakeResult(None)
            matched = [v for v in self.store["versions"] if str(v.chapter_id) == str(vals.get("chapter_id"))]
            matched.sort(key=lambda v: v.version, reverse=_is_desc(stmt))
            if _is_desc(stmt):
                return FakeResult(matched[0] if matched else None)
            return FakeResult(matched)

        if entity is Book:
            return FakeResult(self.store.get("book"))
        if entity is EditorialReviewRound:
            if "id" in vals:
                for r in self.store["rounds"]:
                    if str(r.id) == str(vals["id"]):
                        return FakeResult(r)
                return FakeResult(None)
            matched = [r for r in self.store["rounds"] if str(r.chapter_id) == str(vals.get("chapter_id"))]
            return FakeResult(sorted(matched, key=lambda r: r.round_no))
        if entity is EditorialAnnotation:
            if "id" in vals:
                for a in self.store["annotations"]:
                    if str(a.id) == str(vals["id"]):
                        return FakeResult(a)
                return FakeResult(None)
            matched = [a for a in self.store["annotations"] if str(a.review_round_id) == str(vals.get("review_round_id"))]
            return FakeResult(matched)
        if entity is ReviewIssue:
            if "id" in vals:
                for i in self.store["issues"]:
                    if str(i.id) == str(vals["id"]):
                        return FakeResult(i)
                return FakeResult(None)
            matched = [i for i in self.store["issues"] if str(i.chapter_id) == str(vals.get("chapter_id"))]
            return FakeResult(matched)
        if entity is EditorialRubricTemplate:
            return FakeResult(self.store.get("rubric"))
        if entity is EditorialReviewPolicy:
            if "book_id" in vals:
                p = self.store.get("policy")
                if p is not None and str(p.book_id) == str(vals["book_id"]):
                    return FakeResult(p)
                return FakeResult(None)
            return FakeResult(self.store.get("policy"))
        return FakeResult(None)

    def add(self, obj):
        if isinstance(obj, EditorialRubricTemplate):
            self.store["rubric"] = obj
        elif isinstance(obj, EditorialReviewPolicy):
            self.store["policy"] = obj
        elif isinstance(obj, EditorialReviewRound):
            self.store["rounds"].append(obj)
        elif isinstance(obj, EditorialAnnotation):
            self.store["annotations"].append(obj)
        elif isinstance(obj, EditorialPreferencePair):
            self.store.setdefault("pairs", []).append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)
        for lst in (self.store["annotations"], self.store["rounds"]):
            if obj in lst:
                lst.remove(obj)

    async def flush(self):
        for lst in (
            [self.store.get("rubric")], [self.store.get("policy")],
            self.store["rounds"], self.store["annotations"],
            self.store.get("pairs", []),
        ):
            for obj in lst:
                if obj is not None and getattr(obj, "id", None) is None:
                    obj.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.fixture()
def store():
    ch = _chapter()
    return {
        "book": _book(),
        "chapters": [ch],
        "versions": [_version(ch)],
        "issues": [_issue(ch)],
        "rounds": [],
        "annotations": [],
        "pairs": [],
        "enqueued": [],
    }


@pytest.fixture()
def client(store, monkeypatch):
    session = FakeSession(store)

    async def fake_db():
        yield session

    async def fake_enqueue(job_name, *args, **kwargs):
        store["enqueued"].append((job_name, args))

    monkeypatch.setattr(editorial_api, "_enqueue_editorial_job", fake_enqueue)

    app = FastAPI()
    app.include_router(editorial_api.router)
    app.dependency_overrides[get_db] = fake_db
    return TestClient(app)


class TestPolicy:
    def test_get_bootstraps_defaults(self, client, store):
        r = client.get(f"/api/books/{BOOK_ID}/editorial/policy")
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "windowed"
        assert data["max_unreviewed_ahead"] == 5
        assert data["rubric_template_id"] is not None

    def test_put_updates_and_rejects_invalid_mode(self, client):
        r = client.put(
            f"/api/books/{BOOK_ID}/editorial/policy",
            json={"mode": "blocking"},
        )
        assert r.status_code == 200
        assert r.json()["mode"] == "blocking"

        r = client.put(
            f"/api/books/{BOOK_ID}/editorial/policy",
            json={"mode": "chaos"},
        )
        assert r.status_code == 422


class TestReviewQueue:
    def test_pending_queue_lists_finalized_unreviewed(self, client):
        r = client.get("/api/editorial/review-queue")
        assert r.status_code == 200
        cards = r.json()
        assert len(cards) == 1
        card = cards[0]
        assert card["chapter_no"] == 31
        assert card["editorial_status"] == "pending_review"
        assert card["ai_issue_count"] == 1
        assert card["book_title"] == "诸天红颜录"

    def test_accepted_filter_excludes_pending(self, client, store):
        store["chapters"][0].editorial_status = "accepted"
        r = client.get("/api/editorial/review-queue?filter=accepted")
        assert r.status_code == 200
        assert len(r.json()) == 1
        r = client.get("/api/editorial/review-queue")
        assert len(r.json()) == 0

    def test_unknown_filter_422(self, client):
        r = client.get("/api/editorial/review-queue?filter=nope")
        assert r.status_code == 422


class TestReviewRoundLifecycle:
    def test_create_round_marks_in_review(self, client, store):
        ch = store["chapters"][0]
        r = client.post(f"/api/chapters/{ch.id}/editorial/reviews")
        assert r.status_code == 201
        data = r.json()
        assert data["round_no"] == 1
        assert data["status"] == "draft"
        assert ch.editorial_status == "in_review"

    def test_create_round_on_accepted_chapter_409(self, client, store):
        store["chapters"][0].editorial_status = "accepted"
        r = client.post(f"/api/chapters/{store['chapters'][0].id}/editorial/reviews")
        assert r.status_code == 409

    def test_create_round_without_version_409(self, client, store):
        store["versions"] = []
        ch = store["chapters"][0]
        r = client.post(f"/api/chapters/{ch.id}/editorial/reviews")
        assert r.status_code == 409

    def test_detail_includes_paragraphs_rubric_issues(self, client, store):
        ch = store["chapters"][0]
        created = client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()
        r = client.get(f"/api/editorial/reviews/{created['id']}")
        assert r.status_code == 200
        d = r.json()
        assert len(d["paragraphs"]) == 3
        assert len(d["rubric"]) == 8
        assert d["rubric"][0]["key"] == "plot"
        assert len(d["ai_issues"]) == 1
        assert d["ai_issues"][0]["disposition"] is None
        assert len(d["version_lineage"]) == 1

    def test_submit_accept_sets_status_and_grade(self, client, store):
        ch = store["chapters"][0]
        created = client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()
        r = client.post(
            f"/api/editorial/reviews/{created['id']}/submit",
            json={"verdict": "accept", "rubric_scores": {
                "plot": 18, "character": 18, "causal": 13, "style": 12,
                "pacing": 9, "dialogue": 7, "immersion": 6, "originality": 5,
            }},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["verdict"] == "accept"
        assert data["score_total"] == 88
        assert data["grade"] == "B"
        assert ch.editorial_status == "accepted"
        assert store["enqueued"]  # analysis job dispatched

    def test_submit_quick_grade(self, client, store):
        ch = store["chapters"][0]
        created = client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()
        r = client.post(
            f"/api/editorial/reviews/{created['id']}/submit",
            json={"verdict": "revise", "quick_grade": "C"},
        )
        assert r.status_code == 200
        assert r.json()["score_total"] == 75
        assert store["chapters"][0].editorial_status == "revision_requested"

    def test_submit_invalid_rubric_key_422(self, client, store):
        ch = store["chapters"][0]
        created = client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()
        r = client.post(
            f"/api/editorial/reviews/{created['id']}/submit",
            json={"verdict": "accept", "rubric_scores": {"plto": 10}},
        )
        assert r.status_code == 422

    def test_double_submit_409(self, client, store):
        ch = store["chapters"][0]
        created = client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()
        client.post(f"/api/editorial/reviews/{created['id']}/submit", json={"verdict": "accept"})
        r = client.post(f"/api/editorial/reviews/{created['id']}/submit", json={"verdict": "accept"})
        assert r.status_code == 409

    def test_submit_invalid_verdict_422(self, client, store):
        ch = store["chapters"][0]
        created = client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()
        r = client.post(
            f"/api/editorial/reviews/{created['id']}/submit",
            json={"verdict": "meh"},
        )
        assert r.status_code == 422


class TestAnnotations:
    def _round(self, client, store) -> str:
        ch = store["chapters"][0]
        return client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()["id"]

    def test_create_annotation_computes_anchor(self, client, store):
        rid = self._round(client, store)
        quote = "你早就知道"
        r = client.post(
            f"/api/editorial/reviews/{rid}/annotations",
            json={
                "annotation_type": "issue",
                "category": "dialogue",
                "severity": "major",
                "paragraph_key": 1,
                "start_offset": 1,
                "end_offset": 1 + len(quote),
                "quoted_text": quote,
                "comment": "对白后不要解释",
                "is_blocking": True,
            },
        )
        assert r.status_code == 201
        ann = store["annotations"][0]
        assert ann.quote_hash is not None
        assert ann.context_before is not None
        assert ann.context_after is not None
        assert ann.paragraph_key == "1"

    def test_annotation_wrong_offsets_422(self, client, store):
        rid = self._round(client, store)
        r = client.post(
            f"/api/editorial/reviews/{rid}/annotations",
            json={
                "annotation_type": "issue",
                "paragraph_key": 1,
                "start_offset": 0,
                "end_offset": 5,
                "quoted_text": "完全不相干的文字",
            },
        )
        assert r.status_code == 422

    def test_annotation_unknown_type_422(self, client, store):
        rid = self._round(client, store)
        r = client.post(
            f"/api/editorial/reviews/{rid}/annotations",
            json={"annotation_type": "rant", "paragraph_key": 0, "quoted_text": "x"},
        )
        assert r.status_code == 422

    def test_praise_forces_praise_severity(self, client, store):
        rid = self._round(client, store)
        r = client.post(
            f"/api/editorial/reviews/{rid}/annotations",
            json={
                "annotation_type": "praise",
                "paragraph_key": 2,
                "quoted_text": "窗外渡口的方向传来梆子声",
                "comment": "环境音用得好",
                "severity": "critical",
            },
        )
        assert r.status_code == 201
        assert store["annotations"][0].severity == "praise"

    def test_direct_edit_creates_preference_pair(self, client, store):
        rid = self._round(client, store)
        old = "“你早就知道。”他说，声音很平。"
        r = client.post(
            f"/api/editorial/reviews/{rid}/annotations",
            json={
                "annotation_type": "direct_edit",
                "category": "style",
                "paragraph_key": 1,
                "quoted_text": old,
                "suggested_text": "“你早就知道。”他把杯子转了半圈。",
                "comment": "动作代替平淡的语气说明",
            },
        )
        assert r.status_code == 201
        pairs = store.get("pairs", [])
        assert len(pairs) == 1
        assert pairs[0].rejected_text == old
        assert pairs[0].chosen_text.startswith("“你早就知道。”他把杯子")
        assert pairs[0].source == "human_direct_edit"

    def test_direct_edit_without_suggestion_422(self, client, store):
        rid = self._round(client, store)
        r = client.post(
            f"/api/editorial/reviews/{rid}/annotations",
            json={
                "annotation_type": "direct_edit",
                "paragraph_key": 0,
                "quoted_text": "沈砚把玉佩按在桌上",
            },
        )
        assert r.status_code == 422

    def test_patch_and_delete_annotation(self, client, store):
        rid = self._round(client, store)
        created = client.post(
            f"/api/editorial/reviews/{rid}/annotations",
            json={"annotation_type": "issue", "comment": "初稿", "paragraph_key": 0,
                  "quoted_text": "沈砚把玉佩按在桌上"},
        ).json()
        r = client.patch(
            f"/api/editorial/annotations/{created['id']}",
            json={"comment": "改后", "severity": "major"},
        )
        assert r.status_code == 200
        assert r.json()["comment"] == "改后"
        r = client.delete(f"/api/editorial/annotations/{created['id']}")
        assert r.status_code == 204
        assert store["annotations"] == []


class TestAiIssueDisposition:
    def test_confirm_disposition(self, client, store):
        ch = store["chapters"][0]
        rid = client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()["id"]
        issue_id = store["issues"][0].id
        r = client.post(f"/api/editorial/reviews/{rid}/ai-issues/{issue_id}/confirm")
        assert r.status_code == 200
        assert r.json()["ai_issue_dispositions"][str(issue_id)] == "confirmed"

    def test_dismiss_then_detail_shows_it(self, client, store):
        ch = store["chapters"][0]
        rid = client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()["id"]
        issue_id = store["issues"][0].id
        client.post(f"/api/editorial/reviews/{rid}/ai-issues/{issue_id}/dismiss")
        detail = client.get(f"/api/editorial/reviews/{rid}").json()
        assert detail["ai_issues"][0]["disposition"] == "dismissed"

    def test_issue_from_other_chapter_404(self, client, store):
        ch = store["chapters"][0]
        rid = client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()["id"]
        other = _issue(_chapter(no=32))
        r = client.post(f"/api/editorial/reviews/{rid}/ai-issues/{other.id}/confirm")
        assert r.status_code == 404


class TestRevision:
    def test_revision_requires_submitted_revise(self, client, store):
        ch = store["chapters"][0]
        rid = client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()["id"]
        # not yet submitted → 409
        r = client.post(f"/api/editorial/reviews/{rid}/revision")
        assert r.status_code == 409
        # accepted verdict → 409
        client.post(f"/api/editorial/reviews/{rid}/submit", json={"verdict": "accept"})
        r = client.post(f"/api/editorial/reviews/{rid}/revision")
        assert r.status_code == 409

    def test_revision_enqueues_and_marks_revising(self, client, store):
        ch = store["chapters"][0]
        rid = client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()["id"]
        client.post(f"/api/editorial/reviews/{rid}/submit", json={"verdict": "revise"})
        r = client.post(f"/api/editorial/reviews/{rid}/revision", json={"remediation_level": "L1"})
        assert r.status_code == 200
        assert r.json()["status"] == "revising"
        assert ch.editorial_status == "revising"
        assert store["enqueued"][-1][0] == "run_editorial_revision_job"

    def test_revision_status_reports_lineage(self, client, store):
        ch = store["chapters"][0]
        rid = client.post(f"/api/chapters/{ch.id}/editorial/reviews").json()["id"]
        r = client.get(f"/api/editorial/reviews/{rid}/revision-status")
        assert r.status_code == 200
        data = r.json()
        assert data["editorial_status"] == "in_review"
        assert data["latest_version"]["version"] == 3
