from __future__ import annotations

from fastapi.testclient import TestClient
from tests.test_helpers import configure_temp_runtime, login
import app.main as main
from app.main import app


def test_entities_page_renders_after_router_split(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    with main.get_conn() as conn:
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", project, "character", "Alice", "[]", "{}", "now", "now"),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get(f"/projects/{project}/entities")

    assert res.status_code == 200
    assert "Alice" in res.text


def test_entity_detail_renders_properties_after_router_split(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    with main.get_conn() as conn:
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", project, "character", "Alice", "[]", '{"age": 18}', "now", "now"),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get(f"/projects/{project}/entities/ent_alice")

    assert res.status_code == 200
    assert "Alice" in res.text
    assert "18" in res.text or "age" in res.text


def test_api_create_get_update_delete_entity(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.post("/api/entities", json={
            "project": "demo",
            "kind": "character",
            "name": "Alice",
            "aliases": ["A"],
            "properties": {"age": 18},
        })
        assert res.status_code == 200
        ent_id = res.json()["id"]

        res = client.get(f"/api/entities/{ent_id}")
        assert res.status_code == 200
        assert res.json()["entity"]["name"] == "Alice"

        res = client.put(f"/api/entities/{ent_id}", json={
            "name": "Alicia",
            "aliases": ["A"],
            "properties": {"age": 19},
        })
        assert res.status_code == 200

        res = client.delete(f"/api/entities/{ent_id}")
        assert res.status_code == 200


def test_entity_update_writes_history(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        create = client.post("/api/entities", json={
            "project": "demo",
            "kind": "character",
            "name": "Alice",
            "properties": {"age": 18},
        })
        ent_id = create.json()["id"]

        res = client.put(f"/api/entities/{ent_id}", json={
            "name": "Alice",
            "aliases": [],
            "properties": {"age": 19},
        })
        assert res.status_code == 200

    with main.get_conn() as conn:
        row = conn.execute(
            "SELECT field, old_value, new_value FROM entity_history WHERE entity_id=? AND field='age'",
            (ent_id,)
        ).fetchone()
    assert row is not None


def test_entity_relations_create_and_delete(tmp_path):
    configure_temp_runtime(tmp_path)
    with main.get_conn() as conn:
        for ent_id, name in [("ent_a", "A"), ("ent_b", "B")]:
            conn.execute(
                "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ent_id, "demo", "character", name, "[]", "{}", "now", "now"),
            )

    with TestClient(app) as client:
        login(client)
        res = client.post("/api/entity-relations", json={
            "project": "demo",
            "source_id": "ent_a",
            "target_id": "ent_b",
            "relation_type": "friend",
            "notes": "test",
        })
        assert res.status_code == 200

    with main.get_conn() as conn:
        rel = conn.execute("SELECT id FROM entity_relations").fetchone()

    with TestClient(app) as client:
        login(client)
        res = client.delete(f"/api/entity-relations/{rel['id']}")
        assert res.status_code == 200


def test_bulk_bind_entities_updates_unbound_links(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    chapters_dir = main.NOVELS_ROOT / project / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    chapter = chapters_dir / "00001-start.md"
    main.write_markdown(
        chapter,
        {"title": "Start", "chapter": "1", "status": "draft"},
        "Hello [[Alice]]",
        project=project,
    )

    with main.get_conn() as conn:
        conn.execute(
            "INSERT INTO entities(id, project, kind, name, aliases, properties, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ent_alice", project, "character", "Alice", "[]", "{}", "now", "now"),
        )

    with TestClient(app) as client:
        login(client)
        res = client.post(f"/api/projects/{project}/bulk-bind-entities", json={})
    assert res.status_code == 200
    assert res.json()["updated"] == 1
    raw = chapter.read_text(encoding="utf-8")
    assert "[[ent_alice|Alice]]" in raw


def test_no_duplicate_entity_routes():
    """Ensure no duplicate (method, path) for entity routes after split."""
    from fastapi.routing import APIRoute

    seen = set()
    duplicates = []
    entity_paths = {
        "/api/entities/{ent_id}/appearances",
        "/api/entities",
        "/api/entities/{ent_id}",
        "/api/entity-relations",
        "/api/entity-relations/{rel_id}",
        "/api/projects/{project}/bulk-bind-entities",
        "/projects/{project}/entities",
        "/projects/{project}/entities/{ent_id}",
    }
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path in entity_paths:
            for method in route.methods:
                key = (method, route.path)
                if key in seen:
                    duplicates.append(key)
                seen.add(key)
    assert duplicates == [], f"Duplicate routes: {duplicates}"
