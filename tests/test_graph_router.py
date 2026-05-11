from __future__ import annotations

from fastapi.testclient import TestClient
from tests.test_helpers import configure_temp_runtime, login
import app.main as main
from app.main import app


def test_graph_page_empty_state(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/graph")
    assert res.status_code == 200
    assert "关系图" in res.text
    assert "暂无实体" in res.text


def test_graph_api_returns_nodes_and_links(tmp_path):
    configure_temp_runtime(tmp_path)
    with main.get_conn() as conn:
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", "demo", "character", "Alice", "[\"A\"]", "{}", "now", "now"),
        )
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_bob", "demo", "character", "Bob", "[]", "{}", "now", "now"),
        )
        conn.execute(
            "INSERT INTO entity_relations(project, source_id, target_id, relation_type, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("demo", "ent_alice", "ent_bob", "friend", "好友", "now"),
        )
        conn.execute(
            "INSERT INTO entity_refs(chapter_path, entity_id, ref_kind, char_offset) VALUES (?, ?, ?, ?)",
            ("/tmp/00001.md", "ent_alice", "mention", 1),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get("/api/projects/demo/graph")

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert len(data["nodes"]) == 2
    assert len(data["links"]) == 1
    alice = next(n for n in data["nodes"] if n["id"] == "ent_alice")
    assert alice["name"] == "Alice"
    assert alice["aliases"] == ["A"]
    assert alice["ref_count"] == 1
    assert alice["degree"] == 1
    assert data["links"][0]["relation_type"] == "friend"


def test_graph_api_filters_kind_and_query(tmp_path):
    configure_temp_runtime(tmp_path)
    with main.get_conn() as conn:
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", "demo", "character", "Alice", "[]", "{}", "now", "now"),
        )
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("loc_city", "demo", "location", "City", "[]", "{}", "now", "now"),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get("/api/projects/demo/graph?kind=character&q=Ali")

    assert res.status_code == 200
    data = res.json()
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["id"] == "ent_alice"


def test_graph_page_renders_data_script(tmp_path):
    configure_temp_runtime(tmp_path)
    with main.get_conn() as conn:
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", "demo", "character", "Alice", "[]", "{}", "now", "now"),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/graph")

    assert res.status_code == 200
    assert "graph-data" in res.text
    assert "Alice" in res.text


def test_no_duplicate_graph_routes():
    routes = [r.path for r in app.routes]
    graph_page_routes = [r for r in routes if r == "/projects/{project}/graph"]
    graph_api_routes = [r for r in routes if r == "/api/projects/{project}/graph"]
    assert len(graph_page_routes) == 1
    assert len(graph_api_routes) == 1
