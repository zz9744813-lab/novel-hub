from fastapi.testclient import TestClient
from tests.test_helpers import configure_temp_runtime, login
import app.main as main
from app.main import app

def test_export_page_renders_projects_after_router_split(tmp_path):
    configure_temp_runtime(tmp_path)
    (main.NOVELS_ROOT / "demo" / "chapters").mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        login(client)
        res = client.get("/export")
    assert res.status_code == 200
    assert 'value="demo"' in res.text

def test_export_project_markdown(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "chapters").mkdir(parents=True, exist_ok=True)
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "hello [[Alice]]", project=project)
    main.list_chapters(project, sync=True)

    with TestClient(app) as client:
        login(client)
        res = client.get(f"/api/projects/{project}/export?format=md")

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    assert "# Start" in res.text
    assert "hello [[Alice]]" in res.text

def test_export_project_txt_strips_wikilinks(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "chapters").mkdir(parents=True, exist_ok=True)
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "hello [[ent_abc|Alice]]", project=project)
    main.list_chapters(project, sync=True)

    with TestClient(app) as client:
        login(client)
        res = client.get(f"/api/projects/{project}/export?format=txt")

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    assert "hello Alice" in res.text
    assert "[[" not in res.text

def test_export_all_redirect_still_works(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/export/all", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert res.headers["location"] == "/api/projects/demo/export?format=md"

def test_export_epub_smoke(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "chapters").mkdir(parents=True, exist_ok=True)
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "hello world", project=project)
    main.list_chapters(project, sync=True)

    with TestClient(app) as client:
        login(client)
        res = client.get(f"/api/projects/{project}/export?format=epub")

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/epub+zip"
    assert len(res.content) > 0

def test_no_duplicate_export_routes():
    routes = []
    for route in app.routes:
        if hasattr(route, "methods") and route.methods:
            methods = tuple(sorted(list(route.methods)))
        else:
            methods = ("GET",)
        path = route.path
        routes.append((methods, path))

    relevant_paths = {
        "/export",
        "/api/projects/{project}/export",
        "/projects/{project}/export/all"
    }
    filtered_routes = [r for r in routes if r[1] in relevant_paths]

    assert len(filtered_routes) == len(set(filtered_routes)), f"Duplicate routes found: {filtered_routes}"
