import pytest
from app.main import app


def test_main_has_no_route_decorators():
    from pathlib import Path
    source = Path("app/main.py").read_text(encoding="utf-8")
    assert "@app.get" not in source
    assert "@app.post" not in source
    assert "@app.put" not in source
    assert "@app.delete" not in source


def test_no_app_imports_main():
    from pathlib import Path
    bad = []
    for path in Path("app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "from app.main import" in text or "import app.main" in text:
            bad.append(str(path))
    assert bad == []


def test_required_routers_registered():
    from fastapi.routing import APIRoute
    paths = {route.path for route in app.routes if isinstance(route, APIRoute)}
    expected = {
        "/projects/{project}/timeline",
        "/projects/{project}/graph",
        "/projects/{project}/threads-board",
        "/projects/{project}/entities/{ent_id}/arc",
        "/projects/{project}/consistency",
        "/api/projects/{project}/consistency/run",
    }
    assert expected <= paths


def test_required_built_assets_exist():
    from pathlib import Path
    for filename in [
        "app/static/css/tailwind.css",
        "app/static/js/editor.bundle.js",
    ]:
        p = Path(filename)
        assert p.exists()
        assert p.stat().st_size > 0
