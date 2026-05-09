from __future__ import annotations
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.services.path_service import project_path
from app.db import get_conn
from app.services.markdown_service import safe_slug
from tests.test_helpers import configure_temp_runtime, login

@pytest.fixture
def client(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as c:
        login(c)
        yield c

def test_outline_legacy_redirect(client):
    project = "DemoProject"
    response = client.get(f"/projects/{project}/outline", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["location"] == f"/projects/{project}/stage/outline"

def test_list_volumes_empty(client):
    project = "DemoProject"
    safe_project = safe_slug(project)
    # Ensure project exists
    root = project_path(safe_project)
    root.mkdir(parents=True, exist_ok=True)
    (root / "chapters").mkdir(exist_ok=True)

    response = client.get(f"/api/projects/{project}/volumes")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert isinstance(data["volumes"], list)

def test_volume_crud_and_sync(client):
    project = "voltest"
    safe_project = safe_slug(project)
    root = project_path(safe_project)
    root.mkdir(parents=True, exist_ok=True)
    chap_dir = root / "chapters"
    chap_dir.mkdir(exist_ok=True)

    # Create physical volume directory
    v1_dir = chap_dir / "Volume1"
    v1_dir.mkdir(exist_ok=True)

    # List volumes - should auto-discover Volume1
    response = client.get(f"/api/projects/{project}/volumes")
    assert response.status_code == 200
    data = response.json()
    assert any(v["slug"] == "Volume1" for v in data["volumes"])

    # Rename volume
    response = client.put(
        f"/api/projects/{project}/volumes/Volume1",
        json={"title": "New Title"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    with get_conn() as conn:
        row = conn.execute("SELECT title FROM volumes WHERE project=? AND slug=?", (safe_project, "Volume1")).fetchone()
        assert row["title"] == "New Title"

def test_reorder_volumes(client):
    project = "reordertest"
    safe_project = safe_slug(project)
    with get_conn() as conn:
        conn.execute("INSERT INTO volumes (project, slug, title, seq) VALUES (?, ?, ?, ?)", (safe_project, "v1", "V1", 1))
        conn.execute("INSERT INTO volumes (project, slug, title, seq) VALUES (?, ?, ?, ?)", (safe_project, "v2", "V2", 2))

    response = client.post(
        f"/api/projects/{project}/volumes/reorder",
        json={"order": ["v2", "v1"]}
    )
    assert response.status_code == 200

    with get_conn() as conn:
        v2 = conn.execute("SELECT seq FROM volumes WHERE project=? AND slug=?", (safe_project, "v2")).fetchone()
        v1 = conn.execute("SELECT seq FROM volumes WHERE project=? AND slug=?", (safe_project, "v1")).fetchone()
        assert v2["seq"] < v1["seq"]
