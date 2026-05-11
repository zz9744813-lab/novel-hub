import pytest
from fastapi.testclient import TestClient

from app import main
from app.db import get_conn
from tests.test_helpers import configure_temp_runtime, login

def test_timeline_page_empty_state(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(main.app) as client:
        login(client)
        res = client.get("/projects/demo/timeline")
    assert res.status_code == 200
    assert "时间线" in res.text
    assert "暂无时间线事件" in res.text

def test_timeline_api_returns_entity_history(tmp_path):
    configure_temp_runtime(tmp_path)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", "demo", "character", "Alice", "[]", "{}", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO file_index(path, project, chapter_int) VALUES (?, ?, ?)",
            ("/tmp/00001-start.md", "demo", 1)
        )
        conn.execute(
            "INSERT INTO entity_history(entity_id, project, chapter_int, field, old_value, new_value, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", "demo", 1, "age", "18", "19", "2026-01-02T00:00:00"),
        )

    with TestClient(main.app) as client:
        login(client)
        res = client.get("/api/projects/demo/timeline")

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["entity_name"] == "Alice"
    assert item["field"] == "age"
    assert item["old_value"] == "18"
    assert item["new_value"] == "19"
    assert item["chapter_filename"] == "00001-start.md"

def test_timeline_page_renders_history(tmp_path):
    configure_temp_runtime(tmp_path)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", "demo", "character", "Alice", "[]", "{}", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO file_index(path, project, chapter_int) VALUES (?, ?, ?)",
            ("/tmp/00001-start.md", "demo", 1)
        )
        conn.execute(
            "INSERT INTO entity_history(entity_id, project, chapter_int, field, old_value, new_value, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", "demo", 1, "age", "18", "19", "2026-01-02T00:00:00"),
        )

    with TestClient(main.app) as client:
        login(client)
        res = client.get("/projects/demo/timeline")
    assert res.status_code == 200
    assert "Alice" in res.text
    assert "18" in res.text
    assert "19" in res.text

def test_no_duplicate_timeline_routes():
    routes = [(route.methods, route.path) for route in main.app.routes if hasattr(route, "methods")]
    timeline_page_routes = [r for r in routes if r[1] == "/projects/{project}/timeline" and "GET" in r[0]]
    timeline_api_routes = [r for r in routes if r[1] == "/api/projects/{project}/timeline" and "GET" in r[0]]
    assert len(timeline_page_routes) == 1
    assert len(timeline_api_routes) == 1
