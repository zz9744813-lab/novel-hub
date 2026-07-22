from fastapi.testclient import TestClient
from tests.test_helpers import configure_temp_runtime, login
from app.main import app

def test_health_route_still_works(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

def test_login_logout_routes_still_work(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        # Initial state should not be authed
        # POST login with correct password
        res = client.post("/login", data={"password": "pw"}, follow_redirects=False)
        assert res.status_code in {302, 303}
        assert "session" in res.headers.get("set-cookie", "").lower()

        # GET logout
        res = client.get("/logout", follow_redirects=False)
        assert res.status_code in {302, 303}

def test_settings_requires_auth_and_renders(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        # Not logged in
        res = client.get("/settings", follow_redirects=False)
        assert res.status_code in {302, 303}
        assert "/login" in res.headers.get("location", "")

        # Logged in
        login(client)
        res = client.get("/settings")
        assert res.status_code == 200
        assert "设置" in res.text

def test_no_duplicate_routes_global():
    from fastapi.routing import APIRoute
    seen = set()
    duplicates = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            for method in route.methods:
                if method in {"HEAD", "OPTIONS"}:
                    continue
                key = (method, route.path)
                if key in seen:
                    duplicates.append(key)
                seen.add(key)
    assert duplicates == []

def test_no_duplicate_basic_routes():
    # Helper to check for duplicate routes
    routes = []
    for route in app.routes:
        if hasattr(route, "methods") and route.methods:
            methods = tuple(sorted(list(route.methods)))
        else:
            methods = ("GET",) # Default for Mount etc if we want to be simple
        path = route.path
        routes.append((methods, path))

    # We filter out static mounts and generic internal routes if any
    relevant_paths = {"/health", "/login", "/logout", "/settings"}
    filtered_routes = [r for r in routes if r[1] in relevant_paths]

    assert len(filtered_routes) == len(set(filtered_routes)), f"Duplicate routes found: {filtered_routes}"
