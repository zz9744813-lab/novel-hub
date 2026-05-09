from fastapi.testclient import TestClient
from tests.test_helpers import configure_temp_runtime, login
import app.main as main
from app.main import app

def test_characters_and_world_legacy_redirects(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/characters", follow_redirects=False)
        assert res.status_code == 301
        assert res.headers["location"] == "/projects/demo/stage/characters"

        res = client.get("/projects/demo/world", follow_redirects=False)
        assert res.status_code == 301
        assert res.headers["location"] == "/projects/demo/stage/worldview"

def test_note_preview_renders_markdown(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "characters").mkdir(parents=True, exist_ok=True)
    note = main.NOVELS_ROOT / project / "characters" / "alice.md"
    note.write_text("---\ntitle: Alice\n---\n角色 **简介**", encoding="utf-8")

    with TestClient(app) as client:
        login(client)
        res = client.get(f"/projects/{project}/notes/characters/alice.md")

    assert res.status_code == 200
    assert "Alice" in res.text or "alice" in res.text
    assert "<strong>简介</strong>" in res.text

def test_note_preview_rejects_invalid_folder(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/notes/hooks/foo.md")
    assert res.status_code == 400

def test_rename_note_updates_file_index_path(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "characters").mkdir(parents=True, exist_ok=True)
    note = main.NOVELS_ROOT / project / "characters" / "alice.md"
    main.write_markdown(note, {"title": "Alice", "status": "draft"}, "body", project=project)

    with TestClient(app) as client:
        login(client)
        res = client.put(f"/projects/{project}/notes/characters/alice.md/rename", json={"name": "Alicia"})

    assert res.status_code == 200
    assert not note.exists()
    new_note = main.NOVELS_ROOT / project / "characters" / "alicia.md"
    assert new_note.exists()
    with main.get_conn() as conn:
        row = conn.execute("SELECT path FROM file_index WHERE path=?", (str(new_note),)).fetchone()
    assert row is not None

def test_delete_note_removes_file_and_index(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "characters").mkdir(parents=True, exist_ok=True)
    note = main.NOVELS_ROOT / project / "characters" / "alice.md"
    main.write_markdown(note, {"title": "Alice", "status": "draft"}, "body", project=project)

    with TestClient(app) as client:
        login(client)
        res = client.delete(f"/projects/{project}/notes/characters/alice.md")

    assert res.status_code == 200
    assert not note.exists()
    with main.get_conn() as conn:
        row = conn.execute("SELECT path FROM file_index WHERE path=?", (str(note),)).fetchone()
    assert row is None

def test_no_duplicate_note_routes():
    routes = []
    for route in app.routes:
        if hasattr(route, "methods") and route.methods:
            methods = tuple(sorted(list(route.methods)))
        else:
            methods = ("GET",)
        path = route.path
        routes.append((methods, path))

    relevant_paths = {
        "/projects/{project}/characters",
        "/projects/{project}/world",
        "/projects/{project}/notes/{folder}/{filename}",
        "/projects/{project}/notes/{folder}/{filename}/rename"
    }
    filtered_routes = [r for r in routes if r[1] in relevant_paths]

    assert len(filtered_routes) == len(set(filtered_routes)), f"Duplicate routes found: {filtered_routes}"
