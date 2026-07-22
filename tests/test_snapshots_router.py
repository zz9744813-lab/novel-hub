from pathlib import Path
from fastapi.testclient import TestClient
from tests.test_helpers import configure_temp_runtime, login
import app.main as main
from app.main import app

def test_manual_snapshot_and_api_diff(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "chapters").mkdir(parents=True, exist_ok=True)
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "old body", project=project)

    with TestClient(app) as client:
        login(client)
        chapters = main.list_chapters(project, sync=True)
        filename = chapters[0]["filename"]

        res = client.post("/api/chapters/snapshot", json={"project": project, "filename": filename, "label": "manual"})
        assert res.status_code == 200

        with main.get_conn() as conn:
            snap = conn.execute("SELECT id FROM snapshots ORDER BY id DESC").fetchone()

        main.write_markdown(Path(chapters[0]["path"]), {"title": "Start", "chapter": "1", "status": "draft"}, "new body", project=project)
        res = client.get(f"/api/snapshots/{snap['id']}/diff")

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "old body" in data["diff"]
    assert "new body" in data["diff"]

def test_snapshot_restore_route_restores_content(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "chapters").mkdir(parents=True, exist_ok=True)
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "original", project=project)

    with TestClient(app) as client:
        login(client)
        chapters = main.list_chapters(project, sync=True)
        filename = chapters[0]["filename"]
        actual_path = Path(chapters[0]["path"])

        client.post("/api/chapters/snapshot", json={"project": project, "filename": filename, "label": "manual"})

        with main.get_conn() as conn:
            snap = conn.execute("SELECT id FROM snapshots ORDER BY id DESC").fetchone()

        main.write_markdown(actual_path, {"title": "Start", "chapter": "1", "status": "draft"}, "changed", project=project)
        res = client.post(f"/api/snapshots/{snap['id']}/restore")

    assert res.status_code == 200
    fm, body = main.read_markdown(actual_path)
    assert "original" in body

def test_backup_listing_and_restore(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "chapters").mkdir(parents=True, exist_ok=True)
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "first", project=project)

    chapters = main.list_chapters(project, sync=True)
    filename = chapters[0]["filename"]
    actual_path = Path(chapters[0]["path"])

    # second write should create backup
    main.write_markdown(actual_path, {"title": "Start", "chapter": "1", "status": "draft"}, "second", project=project)

    with TestClient(app) as client:
        login(client)
        res = client.get(f"/projects/{project}/backups/{filename}")
        assert res.status_code == 200
        backups = res.json()["backups"]
        assert backups
        backup_name = backups[0]["name"]

        res = client.get(f"/projects/{project}/diff/{backup_name}")
        assert res.status_code == 200

        res = client.post(f"/projects/{project}/backups/{backup_name}/restore")
        assert res.status_code == 200

def test_no_duplicate_snapshot_routes():
    routes = []
    for route in app.routes:
        if hasattr(route, "methods") and route.methods:
            methods = tuple(sorted(list(route.methods)))
        else:
            methods = ("GET",)
        path = route.path
        routes.append((methods, path))

    relevant_paths = {
        "/projects/{project}/backups/{filename}",
        "/projects/{project}/diff/{backup_name}",
        "/api/snapshots/{snap_id}/restore",
        "/projects/{project}/backups/{backup_name}/restore",
        "/api/chapters/snapshot",
        "/projects/{project}/snapshots/{snap_id}/diff",
        "/api/snapshots/{snap_id}/diff"
    }
    filtered_routes = [r for r in routes if r[1] in relevant_paths]

    assert len(filtered_routes) == len(set(filtered_routes)), f"Duplicate routes found: {filtered_routes}"
