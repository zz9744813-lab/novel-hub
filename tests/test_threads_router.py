from fastapi.testclient import TestClient

from app.main import app
from app import config
from tests.test_helpers import configure_temp_runtime, login


def test_threads_board_empty_state(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/threads-board")
    assert res.status_code == 200
    assert "伏笔板" in res.text
    assert "暂无伏笔" in res.text


def test_threads_api_reads_hooks_markdown(tmp_path):
    configure_temp_runtime(tmp_path)
    hook = config.NOVELS_ROOT / "demo" / "hooks" / "secret.md"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "---\n"
        "title: 神秘信件\n"
        "status: active\n"
        "priority: high\n"
        "chapter: '3'\n"
        "payoff_chapter: '20'\n"
        "tags:\n"
        "  - 主线\n"
        "---\n"
        "这是一条重要伏笔。",
        encoding="utf-8",
    )

    with TestClient(app) as client:
        login(client)
        res = client.get("/api/projects/demo/threads-board")

    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["total"] == 1
    assert len(data["columns"]["active"]) == 1
    thread = data["columns"]["active"][0]
    assert thread["title"] == "神秘信件"
    assert thread["priority"] == "high"
    assert thread["chapter"] == "3"
    assert thread["payoff_chapter"] == "20"
    assert "主线" in thread["tags"]


def test_create_update_delete_thread(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.post("/api/projects/demo/threads", json={
            "title": "失踪的钥匙",
            "body": "第一章出现。",
            "status": "open",
            "priority": "normal",
        })
        assert res.status_code == 200
        thread = res.json()["thread"]
        filename = thread["filename"]
        assert (config.NOVELS_ROOT / "demo" / "hooks" / filename).exists()

        res = client.put(f"/api/projects/demo/threads/{filename}", json={
            "title": "失踪的钥匙",
            "body": "已经推进。",
            "status": "active",
            "priority": "high",
            "tags": ["道具"],
        })
        assert res.status_code == 200
        assert res.json()["thread"]["status"] == "active"

        res = client.delete(f"/api/projects/demo/threads/{filename}")
        assert res.status_code == 200
        assert not (config.NOVELS_ROOT / "demo" / "hooks" / filename).exists()


def test_create_thread_requires_title(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.post("/api/projects/demo/threads", json={"body": "x"})
    assert res.status_code == 400


def test_update_thread_rejects_invalid_status(tmp_path):
    configure_temp_runtime(tmp_path)
    hook = config.NOVELS_ROOT / "demo" / "hooks" / "secret.md"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("---\ntitle: Secret\nstatus: open\n---\nbody", encoding="utf-8")

    with TestClient(app) as client:
        login(client)
        res = client.put("/api/projects/demo/threads/secret.md", json={"status": "bad"})
    assert res.status_code == 400


def test_no_duplicate_threads_routes():
    from app.main import app
    paths = [(route.methods, route.path) for route in app.routes if hasattr(route, "methods")]

    threads_routes = [r for r in paths if "threads" in r[1]]

    # Check for duplicates based on method and path
    seen = set()
    for methods, path in threads_routes:
        for method in methods:
            if method != "HEAD": # Ignore head routes typically added by fastAPI for GET
                key = (method, path)
                assert key not in seen, f"Duplicate route found: {key}"
                seen.add(key)
