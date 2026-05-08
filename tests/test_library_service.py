from tests.test_helpers import configure_temp_runtime
import app.main as main

def test_list_chapters_sync_updates_file_index(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntitle: Start\nchapter: '1'\nstatus: draft\nsynopsis: Opening\n---\nhello world", encoding="utf-8")
    rows = main.list_chapters(project, sync=True)
    assert len(rows) == 1
    assert rows[0]["filename"] == "00001-start.md"
    assert rows[0]["synopsis"] == "Opening"
    with main.get_conn() as conn:
        row = conn.execute("SELECT title, synopsis FROM file_index WHERE path=?", (str(path),)).fetchone()
    assert row["title"] == "Start"
    assert row["synopsis"] == "Opening"

def test_scan_projects_keeps_expected_shape(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    path = main.NOVELS_ROOT / project / "chapters" / "00001-start.md"
    main.write_markdown(path, {"title": "Start", "chapter": "1", "status": "draft"}, "hello world", project=project)
    projects = main.scan_projects()
    assert len(projects) == 1
    p = projects[0]
    for key in [
        "name", "status", "chapter_count", "character_count", "location_count",
        "thread_count", "world_count", "total_words", "target_words",
        "progress", "latest", "author", "synopsis", "daily_goal"
    ]:
        assert key in p
    assert p["name"] == project
    assert p["chapter_count"] == 1

def test_list_notes_reads_markdown_folder(tmp_path):
    configure_temp_runtime(tmp_path)
    note = main.NOVELS_ROOT / "demo" / "characters" / "alice.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("---\ntitle: Alice\n---\n角色简介内容", encoding="utf-8")
    notes = main.list_notes("demo", "characters")
    assert len(notes) == 1
    assert notes[0]["name"] == "alice"
    assert notes[0]["word_count"] > 0
    assert "角色简介" in notes[0]["excerpt"]
