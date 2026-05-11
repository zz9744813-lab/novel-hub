from __future__ import annotations

from fastapi.testclient import TestClient
from tests.test_helpers import configure_temp_runtime, login
import app.main as main
from app.main import app


def test_prompt_get_set_routes(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)

        res = client.put("/api/prompts/demo/premise", json={"content": "阶段提示"})
        assert res.status_code == 200

        res = client.get("/api/prompts/demo/premise")
        assert res.status_code == 200
        assert res.json()["content"] == "阶段提示"


def test_global_prompt_set_route(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.put("/api/prompts/global", json={"content": "全局提示"})
    assert res.status_code == 200

    from app.services.prompts_service import get_global_prompt
    assert get_global_prompt(main.get_setting) == "全局提示"


def test_stage_done_route(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.put("/api/stages/demo/premise/done", json={"done": True})
        assert res.status_code == 200

        res = client.put("/api/stages/demo/premise/done", json={"done": False})
        assert res.status_code == 200


def test_stage_page_premise_renders(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    folder = main.NOVELS_ROOT / project / ".workflow"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "premise.md").write_text("test_premise_content_xyz", encoding="utf-8")

    with TestClient(app) as client:
        login(client)
        res = client.get(f"/projects/{project}/stage/premise")

    assert res.status_code == 200
    assert "test_premise_content_xyz" in res.text


def test_stage_page_writing_redirects_to_project(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/stage/writing", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/projects/demo"


def test_save_workflow_stage_content(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.put("/api/projects/demo/workflow/premise/content", json={"content": "新的立意"})
    assert res.status_code == 200
    path = main.NOVELS_ROOT / "demo" / ".workflow" / "premise.md"
    assert path.read_text(encoding="utf-8") == "新的立意"


def test_invalid_stage_rejected(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.get("/api/prompts/demo/not-a-stage")
    assert res.status_code == 400


def test_workflow_template_globals_registered_after_cleanup(tmp_path):
    configure_temp_runtime(tmp_path)
    from app.deps import get_templates
    templates = get_templates()
    assert "WORKFLOW_STAGES" in templates.env.globals
    assert "stage_label" in templates.env.globals
    assert "project_stage_status_map" in templates.env.globals
    assert "project_next_stage" in templates.env.globals

def test_no_duplicate_workflow_routes():
    """Ensure no duplicate (method, path) for workflow routes after split."""
    from fastapi.routing import APIRoute

    seen = set()
    duplicates = []
    workflow_paths = {
        "/api/prompts/{project}/{stage}",
        "/api/prompts/global",
        "/api/stages/{project}/{stage}/done",
        "/projects/{project}/stage/{stage}",
        "/api/projects/{project}/workflow/{stage}/content",
    }
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path in workflow_paths:
            for method in route.methods:
                key = (method, route.path)
                if key in seen:
                    duplicates.append(key)
                seen.add(key)
    assert duplicates == [], f"Duplicate routes: {duplicates}"
