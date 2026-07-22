from __future__ import annotations

from fastapi.testclient import TestClient

import app.config as config
import app.main as main
import app.routers.ai as ai_router
from app.db import set_setting_encrypted
from app.main import app
from tests.test_helpers import configure_temp_runtime, login


def _enable_ai():
    config.FEATURES["ai"] = True
    main.FEATURES["ai"] = True


def _disable_ai():
    config.FEATURES["ai"] = False
    main.FEATURES["ai"] = False


def test_stage_premise_ai_uses_router_and_mocked_client(tmp_path, monkeypatch):
    configure_temp_runtime(tmp_path)
    _enable_ai()

    async def fake_generate(api_key, base_url, model, system_prompt, user_prompt):
        assert api_key == "test-key"
        assert "当前草稿" in user_prompt
        return "AI premise feedback"

    monkeypatch.setattr(ai_router, "generate_ai_content", fake_generate)
    set_setting_encrypted("ai_api_key", "test-key")

    with TestClient(app) as client:
        login(client)
        res = client.post(
            "/api/projects/demo/stage/premise/ai",
            json={"action": "discuss", "current": "draft idea"},
        )

    assert res.status_code == 200
    assert res.json() == {"status": "ok", "text": "AI premise feedback"}
    _disable_ai()


def test_chapter_outline_ai_writes_no_external_call(tmp_path, monkeypatch):
    configure_temp_runtime(tmp_path)
    _enable_ai()
    project = "demo"
    chapters_dir = main.NOVELS_ROOT / project / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    main.write_markdown(
        chapters_dir / "00001-start.md",
        {"title": "Start", "chapter": "1", "status": "draft"},
        "body text",
        project=project,
    )
    main.list_chapters(project, sync=True)

    async def fake_generate(api_key, base_url, model, system_prompt, user_prompt):
        assert "本章标题:Start" in user_prompt
        return "章节梗概"

    monkeypatch.setattr(ai_router, "generate_ai_content", fake_generate)
    set_setting_encrypted("ai_api_key", "test-key")

    with TestClient(app) as client:
        login(client)
        res = client.post(
            f"/api/projects/{project}/stage/chapter_outline/ai",
            json={"filename": "00001-start.md"},
        )

    assert res.status_code == 200
    assert res.json() == {"status": "ok", "text": "章节梗概"}
    _disable_ai()


def test_ai_generate_stream_uses_mocked_stream(tmp_path, monkeypatch):
    configure_temp_runtime(tmp_path)
    _enable_ai()
    project = "demo"
    (main.NOVELS_ROOT / project / "chapters").mkdir(parents=True, exist_ok=True)

    async def fake_stream(api_key, base_url, model, system_prompt, user_prompt):
        assert api_key == "test-key"
        yield "第一段"
        yield "第二段"

    monkeypatch.setattr(ai_router, "generate_ai_content_stream", fake_stream)
    set_setting_encrypted("ai_api_key", "test-key")

    with TestClient(app) as client:
        login(client)
        res = client.get(f"/api/projects/{project}/ai/generate?mode=continue&text=hello")

    assert res.status_code == 200
    assert "data: " in res.text
    assert "\\u7b2c\\u4e00\\u6bb5" in res.text
    assert "\\u7b2c\\u4e8c\\u6bb5" in res.text
    assert "data: [DONE]" in res.text
    _disable_ai()


def test_no_duplicate_ai_routes():
    """Ensure no duplicate (method, path) for AI routes after split."""
    from fastapi.routing import APIRoute

    seen = set()
    duplicates = []
    ai_paths = {
        "/api/projects/{project}/ai/outline/volume",
        "/api/projects/{project}/ai/outline/chapter",
        "/api/projects/{project}/ai/outline/scene",
        "/api/projects/{project}/ai/outline/draft",
        "/api/projects/{project}/ai/generate",
        "/api/projects/{project}/stage/premise/ai",
        "/api/projects/{project}/stage/worldview/ai",
        "/api/projects/{project}/stage/characters/ai",
        "/api/projects/{project}/stage/chapter_outline/ai",
    }
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path in ai_paths:
            for method in route.methods:
                key = (method, route.path)
                if key in seen:
                    duplicates.append(key)
                seen.add(key)
    assert duplicates == [], f"Duplicate routes: {duplicates}"
