import inspect
from pathlib import Path
from starlette.testclient import TestClient

import app.main as main
from app.main import app
from tests.test_helpers import configure_temp_runtime, login


def test_api_csrf_exemption_removed():
    source = inspect.getsource(main)
    assert 'r"^/api/.*"' not in source
    assert "ENABLE_CSRF" in source


def test_base_includes_csrf_helper(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.get("/")
    assert res.status_code == 200

    js = Path("app/static/js/ui.js").read_text(encoding="utf-8")
    assert "csrfFetch" in js
    assert "htmx:configRequest" in js
    assert "X-CSRFToken" in js


def test_mutating_fetches_use_csrf_helper():
    roots = [Path("app/templates"), Path("app/static/js")]
    bad = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix not in {".html", ".js"}:
                continue
            text = path.read_text(encoding="utf-8")
            if "fetch(" in text and any(m in text for m in [
                "method: 'POST'", 'method: "POST"', "method:'POST'", 'method:"POST"',
                "method: 'PUT'", "method: 'DELETE'", "method:'DELETE'", "method:'PUT'"
            ]):
                # allow explicit csrfFetch or X-CSRFToken
                if "csrfFetch(" not in text and "X-CSRFToken" not in text:
                    bad.append(str(path))
    assert bad == []


def test_readme_updated():
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "CSRF 中间件目前明确停用" not in readme
