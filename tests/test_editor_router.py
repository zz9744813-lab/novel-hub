from pathlib import Path
from fastapi.testclient import TestClient
from tests.test_helpers import configure_temp_runtime, login
import app.main as main
from app.main import app

def test_editor_page_renders_after_router_split(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "chapters").mkdir(parents=True, exist_ok=True)
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "hello world", project=project)

    chapters = main.list_chapters(project, sync=True)
    filename = chapters[0]["filename"]

    with TestClient(app) as client:
        login(client)
        res = client.get(f"/projects/{project}/editor/{filename}")

    assert res.status_code == 200
    assert "Start" in res.text
    assert "hello world" in res.text

def test_save_chapter_route_updates_file_and_logs_words(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "chapters").mkdir(parents=True, exist_ok=True)
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "hello", project=project)

    chapters = main.list_chapters(project, sync=True)
    filename = chapters[0]["filename"]
    actual_path = Path(chapters[0]["path"])
    mtime = actual_path.stat().st_mtime

    with TestClient(app) as client:
        login(client)
        res = client.post(
            f"/projects/{project}/editor/{filename}",
            data={
                "title": "Start Updated",
                "chapter": "1",
                "status": "draft",
                "volume": "",
                "tags": "",
                "synopsis": "Updated synopsis",
                "notes": "",
                "pov": "",
                "characters": "",
                "locations": "",
                "warnings": "",
                "draft_version": "v1",
                "body": "hello world again",
                "loaded_mtime": str(mtime),
            },
        )

    assert res.status_code == 200
    # Refresh index and get actual path (it might have moved to volume directory)
    chapters = main.list_chapters(project, sync=True)
    actual_path = Path(chapters[0]["path"])
    fm, body = main.read_markdown(actual_path)
    assert fm.get("title") == "Start Updated"
    assert fm.get("synopsis") == "Updated synopsis"
    assert "hello world again" in body
    with main.get_conn() as conn:
        row = conn.execute("SELECT action, value FROM operation_logs WHERE action='save'").fetchone()
    assert row is not None
    assert row["value"] >= 0

def test_save_chapter_conflict_returns_conflict_markup(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "chapters").mkdir(parents=True, exist_ok=True)
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "old", project=project)

    chapters = main.list_chapters(project, sync=True)
    filename = chapters[0]["filename"]
    actual_path = Path(chapters[0]["path"])
    stale_mtime = actual_path.stat().st_mtime

    # Simulate external edit (updates mtime)
    import time
    time.sleep(1.1)
    main.write_markdown(actual_path, {"title": "Start", "chapter": "1", "status": "draft"}, "external change", project=project)

    with TestClient(app) as client:
        login(client)
        res = client.post(
            f"/projects/{project}/editor/{filename}",
            data={
                "title": "Start",
                "chapter": "1",
                "status": "draft",
                "volume": "",
                "tags": "",
                "synopsis": "",
                "notes": "",
                "pov": "",
                "characters": "",
                "locations": "",
                "warnings": "",
                "draft_version": "v1",
                "body": "my change",
                "loaded_mtime": str(stale_mtime),
            },
        )

    assert res.status_code == 200
    assert "chapter_conflict" in res.text or "文件已被其他来源修改" in res.text

def test_save_chapter_force_allows_overwrite(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    (main.NOVELS_ROOT / project / "chapters").mkdir(parents=True, exist_ok=True)
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "old", project=project)

    chapters = main.list_chapters(project, sync=True)
    filename = chapters[0]["filename"]
    actual_path = Path(chapters[0]["path"])
    stale_mtime = actual_path.stat().st_mtime

    import time
    time.sleep(1.1)
    main.write_markdown(actual_path, {"title": "Start", "chapter": "1", "status": "draft"}, "external", project=project)

    with TestClient(app) as client:
        login(client)
        res = client.post(
            f"/projects/{project}/editor/{filename}",
            data={
                "title": "Forced",
                "chapter": "1",
                "status": "draft",
                "volume": "",
                "tags": "",
                "synopsis": "",
                "notes": "",
                "pov": "",
                "characters": "",
                "locations": "",
                "warnings": "",
                "draft_version": "v1",
                "body": "forced body",
                "loaded_mtime": str(stale_mtime),
                "force": "true",
            },
        )

    assert res.status_code == 200
    chapters = main.list_chapters(project, sync=True)
    actual_path = Path(chapters[0]["path"])
    fm, body = main.read_markdown(actual_path)
    assert fm.get("title") == "Forced"
    assert body.strip() == "forced body"

def test_no_duplicate_editor_routes():
    routes = []
    for route in app.routes:
        if hasattr(route, "methods") and route.methods:
            methods = tuple(sorted(list(route.methods)))
        else:
            methods = ("GET",)
        path = route.path
        routes.append((methods, path))

    relevant_paths = {"/projects/{project}/editor/{filename}"}
    filtered_routes = [r for r in routes if r[1] in relevant_paths]

    assert len(filtered_routes) == len(set(filtered_routes)), f"Duplicate routes found: {filtered_routes}"
