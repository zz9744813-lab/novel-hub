from fastapi.testclient import TestClient

from app.main import app
from app import db
from tests.test_helpers import configure_temp_runtime, login


def test_entity_arc_page_empty_state(tmp_path):
    configure_temp_runtime(tmp_path)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", "demo", "character", "Alice", "[]", "{}", "now", "now"),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/entities/ent_alice/arc")

    assert res.status_code == 200
    assert "角色弧光" in res.text
    assert "暂无弧光记录" in res.text


def test_entity_arc_api_returns_history(tmp_path):
    configure_temp_runtime(tmp_path)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", "demo", "character", "Alice", "[]", "{}", "now", "now"),
        )
        conn.execute(
            "INSERT INTO file_index(path, project, title, chapter_int) VALUES (?, ?, ?, ?)",
            ("/tmp/00003-test.md", "demo", "第三章", 3),
        )
        conn.execute(
            "INSERT INTO entity_history(entity_id, project, chapter_int, field, old_value, new_value, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", "demo", 3, "age", "18", "19", "2026-01-02T00:00:00"),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get("/api/entities/ent_alice/arc")

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["entity"]["name"] == "Alice"
    assert data["stats"]["event_count"] == 1
    assert data["events"][0]["field"] == "age"
    assert data["events"][0]["chapter_title"] == "第三章"
    assert data["events"][0]["chapter_filename"] == "00003-test.md"
    assert "age" in data["field_groups"]


def test_entity_arc_page_rejects_wrong_project(tmp_path):
    configure_temp_runtime(tmp_path)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", "demo", "character", "Alice", "[]", "{}", "now", "now"),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/other/entities/ent_alice/arc")

    assert res.status_code == 404


def test_entity_detail_has_arc_link(tmp_path):
    configure_temp_runtime(tmp_path)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", "demo", "character", "Alice", "[]", "{}", "now", "now"),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/entities/ent_alice")

    assert res.status_code == 200
    assert "/projects/demo/entities/ent_alice/arc" in res.text
    assert "角色弧光" in res.text


def test_no_duplicate_arc_routes():
    from app.main import app
    routes = []
    for r in app.routes:
        if hasattr(r, "methods") and hasattr(r, "path"):
            for m in r.methods:
                routes.append((m, r.path))

    # ensure count is exactly 1 for each
    assert routes.count(("GET", "/projects/{project}/entities/{ent_id}/arc")) == 1
    assert routes.count(("GET", "/api/entities/{ent_id}/arc")) == 1
