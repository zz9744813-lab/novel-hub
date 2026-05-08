import pytest
from fastapi.testclient import TestClient
from pathlib import Path
import app.main as main
from tests.test_helpers import configure_temp_runtime, login, write_markdown

def test_status_label_helper():
    assert main.status_label("draft") == "草稿"
    assert main.status_label("published") == "已发布"
    assert main.status_label("idea") == "灵感"
    assert main.status_label("unknown") == "unknown"
    assert main.status_label(None) == ""

def test_editor_renders_localized_status(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    chapters_dir = main.NOVELS_ROOT / project / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    write_markdown(
        chapters_dir / "00001-start.md",
        {"title": "Start", "chapter": "1", "status": "draft"},
        "hello world",
        project=project,
    )

    with TestClient(main.app) as client:
        login(client)
        res = client.get(f"/projects/{project}/editor/00001-start.md")
        assert res.status_code == 200
        # Check for localized label. We match with flexible spacing if needed, but here we just check presence.
        assert '草稿' in res.text
        assert '已发布' in res.text
        # Ensure value is still English
        assert 'value="draft"' in res.text
        assert 'value="published"' in res.text

def test_snapshot_diff_api_basic(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    chapters_dir = main.NOVELS_ROOT / project / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    path = chapters_dir / "00001-start.md"
    write_markdown(path, {"title": "Start", "status": "draft"}, "version 1", project=project)

    main.backup_file(path, label="snap1")

    write_markdown(path, {"title": "Start", "status": "draft"}, "version 2", project=project)

    with main.get_conn() as conn:
        snap = conn.execute("SELECT id FROM snapshots WHERE chapter_path=?", (str(path),)).fetchone()
        snap_id = snap["id"]

    with TestClient(main.app) as client:
        login(client)
        res = client.get(f"/api/snapshots/{snap_id}/diff")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "version 1" in data["diff"]
        assert "version 2" in data["diff"]

def test_snapshot_diff_api_auth(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(main.app) as client:
        res = client.get("/api/snapshots/1/diff")
        assert res.status_code in {401, 302, 303} # Auth redirect or unauthorized

def test_snapshot_diff_not_found(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(main.app) as client:
        login(client)
        res = client.get("/api/snapshots/999/diff")
        assert res.status_code == 404
