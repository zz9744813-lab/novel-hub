from pathlib import Path
from fastapi.testclient import TestClient

from app.main import app
from tests.test_helpers import configure_temp_runtime, login

def test_base_page_uses_local_tailwind_css(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.get("/")
    assert res.status_code == 200
    assert "/static/css/tailwind.css" in res.text
    assert "cdn.tailwindcss.com" not in res.text

def test_editor_page_uses_local_codemirror_bundle(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    from app import main
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    from app.services.chapter_service import write_markdown
    write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "hello", project=project)

    with TestClient(app) as client:
        login(client)
        res = client.get(f"/projects/{project}/editor/00001-start.md")

    assert res.status_code == 200
    assert "/static/js/editor.bundle.js" in res.text
    assert "esm.sh" not in res.text
    assert "importmap" not in res.text

def test_built_static_assets_exist():
    css = Path("app/static/css/tailwind.css")
    js = Path("app/static/js/editor.bundle.js")
    assert css.exists()
    assert css.stat().st_size > 0
    assert js.exists()
    assert js.stat().st_size > 0
