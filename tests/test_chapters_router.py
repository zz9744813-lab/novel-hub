from fastapi.testclient import TestClient
from tests.test_helpers import configure_temp_runtime, login
import app.main as main
from app.main import app

def test_create_chapter_route_redirects_to_editor(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.post(
            "/projects/demo/chapters/new",
            data={"title": "第一章", "chapter_number": "1", "status": "draft"},
            follow_redirects=False,
        )
    assert res.status_code in {302, 303}
    assert "/projects/demo/editor/" in res.headers["location"]
    assert any((main.NOVELS_ROOT / "demo" / "chapters").rglob("*.md"))

def test_chapter_read_only_route_renders_markdown(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "hello **world**", project=project)
    with TestClient(app) as client:
        login(client)
        res = client.get(f"/projects/{project}/chapters/00001-start.md/read")
    assert res.status_code == 200
    assert "Start" in res.text
    assert "<strong>world</strong>" in res.text

def test_sidebar_chapters_route_renders_active_list(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "hello world", project=project)
    with TestClient(app) as client:
        login(client)
        res = client.get(f"/projects/{project}/sidebar_chapters?active=00001-start.md")
    assert res.status_code == 200
    assert "Start" in res.text or "00001-start.md" in res.text

def test_preview_route_still_works_after_split(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.post("/projects/demo/preview", data={"body": "hello **world**"})
    assert res.status_code == 200
    assert "<strong>world</strong>" in res.text

def test_reorder_chapters_route_updates_chapter_numbers(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    first = main.NOVELS_ROOT / project / "chapters" / "00001-first.md"
    second = main.NOVELS_ROOT / project / "chapters" / "00002-second.md"
    main.write_markdown(first, {"title": "First", "chapter": "1", "status": "draft"}, "first", project=project)
    main.write_markdown(second, {"title": "Second", "chapter": "2", "status": "draft"}, "second", project=project)

    with TestClient(app) as client:
        login(client)
        # Reorder second before first
        res = client.post(f"/projects/{project}/chapters/reorder", json={"order": ["00002-second.md", "00001-first.md"]})
    assert res.status_code == 200
    chapters = main.list_chapters(project, sync=True)
    # The first in list should now be "second.md"
    assert "second" in chapters[0]["filename"]
    assert chapters[0]["chapter"] == "1"

def test_no_duplicate_chapter_routes():
    routes = []
    for route in app.routes:
        if hasattr(route, "methods") and route.methods:
            methods = tuple(sorted(list(route.methods)))
        else:
            methods = ("GET",)
        path = route.path
        routes.append((methods, path))

    relevant_paths = {
        "/projects/{project}/chapters/new",
        "/projects/{project}/chapters/{filename}",
        "/projects/{project}/chapters/{filename}/rename",
        "/projects/{project}/chapters",
        "/projects/{project}/chapters/{filename}/read",
        "/projects/{project}/sidebar_chapters",
        "/projects/{project}/chapters/reorder",
        "/projects/{project}/preview"
    }
    filtered_routes = [r for r in routes if r[1] in relevant_paths]

    assert len(filtered_routes) == len(set(filtered_routes)), f"Duplicate routes found: {filtered_routes}"
