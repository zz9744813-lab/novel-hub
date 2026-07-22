from __future__ import annotations

from fastapi.testclient import TestClient
from tests.test_helpers import configure_temp_runtime, login
import app.main as main
import app.config as config
from app.main import app


def _enable_scenes():
    """Enable scenes feature flag for testing."""
    config.FEATURES["scenes"] = True
    main.FEATURES["scenes"] = True


def test_create_list_update_delete_scene(tmp_path):
    configure_temp_runtime(tmp_path)
    _enable_scenes()
    with TestClient(app) as client:
        login(client)
        res = client.post("/api/projects/demo/scenes", json={
            "chapter_path": "/tmp/demo/chapters/00001-start.md",
            "seq": 1,
            "title": "第一场",
        })
        assert res.status_code == 200
        sc_id = res.json()["id"]

        res = client.get("/api/projects/demo/scenes")
        assert res.status_code == 200
        scenes = res.json()["scenes"]
        assert any(s["id"] == sc_id for s in scenes)

        res = client.put(f"/api/scenes/{sc_id}", json={
            "title": "第一场修订",
            "pov": "Alice",
            "location_id": "loc_1",
            "summary": "summary",
            "status": "done",
        })
        assert res.status_code == 200

        res = client.delete(f"/api/scenes/{sc_id}")
        assert res.status_code == 200

    with main.get_conn() as conn:
        row = conn.execute("SELECT id FROM scenes WHERE id=?", (sc_id,)).fetchone()
    assert row is None


def test_list_scenes_filters_by_chapter(tmp_path):
    configure_temp_runtime(tmp_path)
    _enable_scenes()
    with main.get_conn() as conn:
        conn.execute(
            "INSERT INTO scenes(id, chapter_path, project, seq, title, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("sc_a", "/x/00001-a.md", "demo", 1, "A", "draft"),
        )
        conn.execute(
            "INSERT INTO scenes(id, chapter_path, project, seq, title, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("sc_b", "/x/00002-b.md", "demo", 2, "B", "draft"),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get("/api/projects/demo/scenes?chapter=00001-a.md")

    assert res.status_code == 200
    scenes = res.json()["scenes"]
    assert len(scenes) == 1
    assert scenes[0]["id"] == "sc_a"


def test_outline_api_returns_volumes_chapters_scenes(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    chapters_dir = main.NOVELS_ROOT / project / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    chapter = chapters_dir / "00001-start.md"
    main.write_markdown(chapter, {"title": "Start", "chapter": "1", "status": "draft"}, "hello", project=project)
    main.list_chapters(project, sync=True)

    with main.get_conn() as conn:
        conn.execute("INSERT INTO volumes(project, slug, title, seq) VALUES (?, ?, ?, ?)", (project, "v1", "第一卷", 1))
        conn.execute(
            "INSERT INTO scenes(id, chapter_path, project, seq, title, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("sc_a", str(chapter), project, 1, "第一场", "draft"),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get(f"/api/projects/{project}/outline")

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "volumes" in data
    assert "chapters" in data
    assert "scenes" in data
    assert len(data["scenes"]) >= 1


def test_no_duplicate_scene_routes():
    """Ensure no duplicate (method, path) for scene/outline routes after split."""
    from fastapi.routing import APIRoute

    seen = set()
    duplicates = []
    scene_paths = {
        "/api/projects/{project}/scenes",
        "/api/scenes/{sc_id}",
        "/api/projects/{project}/outline",
    }
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path in scene_paths:
            for method in route.methods:
                key = (method, route.path)
                if key in seen:
                    duplicates.append(key)
                seen.add(key)
    assert duplicates == [], f"Duplicate routes: {duplicates}"
