from fastapi.testclient import TestClient
from app.main import app
from app import main
from tests.test_helpers import configure_temp_runtime, login

def test_consistency_page_empty_state(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/consistency")

    assert res.status_code == 200
    assert "一致性检查" in res.text
    assert "暂无一致性检查报告" in res.text

def test_consistency_api_returns_reports(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    chapter_dir = main.NOVELS_ROOT / project / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    chapter = chapter_dir / "00001-start.md"
    main.write_markdown(chapter, {"title": "Start", "chapter": "1", "status": "draft"}, "hello", project=project)

    with main.get_conn() as conn:
        conn.execute(
            "INSERT INTO consistency_reports(chapter_path, created_at, issues) VALUES (?, ?, ?)",
            (str(chapter), "2026-01-02T00:00:00", '[{"severity":"warning","message":"人物年龄不一致"}]'),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get("/api/projects/demo/consistency")

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["summary"]["total_reports"] == 1
    assert data["summary"]["total_issues"] == 1
    assert data["reports"][0]["issue_count"] == 1
    assert data["reports"][0]["severity"] == "warning"
    assert data["reports"][0]["chapter_filename"] == "00001-start.md"

def test_consistency_page_renders_report(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    chapter_dir = main.NOVELS_ROOT / project / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    chapter = chapter_dir / "00001-start.md"
    main.write_markdown(chapter, {"title": "Start", "chapter": "1", "status": "draft"}, "hello", project=project)

    with main.get_conn() as conn:
        conn.execute(
            "INSERT INTO consistency_reports(chapter_path, created_at, issues) VALUES (?, ?, ?)",
            (str(chapter), "2026-01-02T00:00:00", '[{"severity":"warning","message":"人物年龄不一致"}]'),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/consistency")

    assert res.status_code == 200
    assert "人物年龄不一致" in res.text

def test_consistency_run_rejects_when_disabled(tmp_path, monkeypatch):
    configure_temp_runtime(tmp_path)
    monkeypatch.setitem(main.FEATURES, "ai_check", False)

    with TestClient(app) as client:
        login(client)
        res = client.post("/api/projects/demo/consistency/run", json={})

    assert res.status_code == 400

def test_consistency_run_single_chapter_queues_task(tmp_path, monkeypatch):
    configure_temp_runtime(tmp_path)
    monkeypatch.setitem(main.FEATURES, "ai_check", True)

    called = []
    async def fake_run(project, path):
        called.append((project, path))

    monkeypatch.setattr("app.routers.consistency.run_consistency_check", fake_run)

    project = "demo"
    chapter_dir = main.NOVELS_ROOT / project / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    chapter = chapter_dir / "00001-start.md"
    main.write_markdown(chapter, {"title": "Start", "chapter": "1", "status": "draft"}, "hello", project=project)

    with TestClient(app) as client:
        login(client)
        res = client.post("/api/projects/demo/consistency/run", json={"filename": "00001-start.md"})

    assert res.status_code == 200
    assert res.json()["queued"] == 1
    assert called and called[0][0] == "demo"

def test_consistency_run_all_queues_all_chapters(tmp_path, monkeypatch):
    configure_temp_runtime(tmp_path)
    monkeypatch.setitem(main.FEATURES, "ai_check", True)

    called = []
    async def fake_run(project, path):
        called.append((project, path))

    monkeypatch.setattr("app.routers.consistency.run_consistency_check", fake_run)

    project = "demo"
    chapter_dir = main.NOVELS_ROOT / project / "chapters"
    chapter_dir.mkdir(parents=True, exist_ok=True)

    for i in [1, 2]:
        chapter = chapter_dir / f"{i:05d}-c{i}.md"
        main.write_markdown(chapter, {"title": f"C{i}", "chapter": str(i), "status": "draft"}, "hello", project=project)

    with TestClient(app) as client:
        login(client)
        res = client.post("/api/projects/demo/consistency/run", json={})

    assert res.status_code == 200
    assert res.json()["queued"] == 2

def test_no_duplicate_consistency_routes():
    from app.routers.consistency import router
    paths = [(tuple(r.methods), r.path) for r in router.routes]
    assert len(paths) == len(set(paths))
    assert (("GET",), "/projects/{project}/consistency") in paths or (("GET", "HEAD"), "/projects/{project}/consistency") in paths
    assert (("GET",), "/api/projects/{project}/consistency") in paths or (("GET", "HEAD"), "/api/projects/{project}/consistency") in paths
    assert (("POST",), "/api/projects/{project}/consistency/run") in paths
