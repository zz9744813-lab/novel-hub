from fastapi.testclient import TestClient
from tests.test_helpers import configure_temp_runtime, login
import app.main as main
from app.main import app

def test_dashboard_route_renders_after_router_split(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.get("/")
    assert res.status_code == 200
    assert "Novel Hub" in res.text or "仪表盘" in res.text

def test_projects_page_and_create_project(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.get("/projects")
        assert res.status_code == 200
        res = client.post("/projects/new", data={"name": "Demo Novel"}, follow_redirects=False)
        assert res.status_code in {302, 303}
        assert (main.NOVELS_ROOT / "demo-novel").exists()

def test_project_detail_route_still_renders(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "hello world", project=project)
    with TestClient(app) as client:
        login(client)
        res = client.get(f"/projects/{project}")
    assert res.status_code == 200
    assert "Start" in res.text or "项目操作" in res.text

def test_project_stats_route_still_uses_metrics(tmp_path):
    configure_temp_runtime(tmp_path)
    main.log_operation("save", "chapter.md", "words_added=123", value=123, project="demo")
    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/stats")
    assert res.status_code == 200
    assert "123" in res.text or "每日字数" in res.text

def test_no_duplicate_project_routes():
    # Helper to check for duplicate routes
    routes = []
    for route in app.routes:
        if hasattr(route, "methods") and route.methods:
            methods = tuple(sorted(list(route.methods)))
        else:
            methods = ("GET",)
        path = route.path
        routes.append((methods, path))

    # Check key routes migrated this round
    relevant_paths = {"/", "/projects", "/projects/new", "/projects/{project}", "/projects/{project}/stats", "/projects/{project}/search"}
    filtered_routes = [r for r in routes if r[1] in relevant_paths]

    assert len(filtered_routes) == len(set(filtered_routes)), f"Duplicate routes found: {filtered_routes}"
