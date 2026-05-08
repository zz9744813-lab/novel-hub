from tests.test_helpers import configure_temp_runtime
import app.main as main

def test_write_markdown_indexes_scenes(tmp_path):
    configure_temp_runtime(tmp_path)
    path = main.NOVELS_ROOT / "demo" / "chapters" / "chapter.md"
    body = "## 第一场\n这里是第一场。\n## 第二场\n这里是第二场。"
    main.write_markdown(path, {"title": "Chapter", "status": "draft"}, body, project="demo")
    with main.get_conn() as conn:
        rows = conn.execute(
            "SELECT seq, title, word_count FROM scenes WHERE chapter_path=? ORDER BY seq",
            (str(path),),
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["seq"] == 1
    assert rows[0]["title"] == "第一场"
    assert rows[1]["seq"] == 2
    assert rows[1]["title"] == "第二场"

def test_write_markdown_indexes_whole_chapter_as_single_scene(tmp_path):
    configure_temp_runtime(tmp_path)
    path = main.NOVELS_ROOT / "demo" / "chapters" / "chapter.md"
    body = "没有二级标题的一整章"
    main.write_markdown(path, {"title": "Chapter", "status": "draft"}, body, project="demo")
    with main.get_conn() as conn:
        rows = conn.execute(
            "SELECT seq, title, word_count FROM scenes WHERE chapter_path=? ORDER BY seq",
            (str(path),),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["seq"] == 1
    assert rows[0]["title"] == "Chapter"
