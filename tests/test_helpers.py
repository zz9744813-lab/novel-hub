import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import app.main as main
from app.main import (
    FEATURES,
    app,
    count_words,
    feature_enabled,
    parse_frontmatter,
    read_markdown,
    safe_slug,
    write_atomic,
    write_markdown,
)


def configure_temp_runtime(tmp_path):
    main.DB_PATH = tmp_path / "novelhub.db"
    os.environ["NOVELHUB_DB_PATH"] = str(main.DB_PATH)
    main.VAULT_ROOT = tmp_path / "vault"
    main.NOVELS_ROOT = main.VAULT_ROOT / "Novels"
    main.BACKUP_ROOT = main.VAULT_ROOT / ".novelhub-backups"
    main.ADMIN_PASSWORD = "pw"
    main.SECRET_KEY = "test-secret"
    main.init_db()


def login(client):
    res = client.post("/login", data={"password": "pw"}, follow_redirects=False)
    assert res.status_code in {302, 303}


def test_safe_slug_basic():
    assert safe_slug("Hello World") == "hello-world"
    assert safe_slug("foo/../bar") == "foobar"


def test_safe_slug_empty():
    assert safe_slug("", fallback="x") == "x"


def test_safe_slug_chinese():
    text = "\u4e2d\u6587\u9879\u76ee"
    assert safe_slug(text) == text


def test_count_words_latin():
    assert count_words("hello world") == 2


def test_count_words_cjk():
    assert count_words("\u4f60\u597d\u4e16\u754c") == 4


def test_count_words_mixed():
    assert count_words("\u6df7\u5408 hello \u6587\u5b57 123") == 4 + 2


def test_count_words_underscore_no_split():
    assert count_words("under_score") == 1


def test_parse_frontmatter_yaml():
    text = "---\ntitle: foo\nstatus: draft\n---\nbody content"
    fm, body = parse_frontmatter(text)
    assert fm["title"] == "foo"
    assert fm["status"] == "draft"
    assert body == "body content"


def test_parse_frontmatter_none():
    fm, body = parse_frontmatter("just body, no frontmatter")
    assert fm == {}
    assert body == "just body, no frontmatter"


def test_feature_flags_default_off():
    assert FEATURES
    assert all(enabled is False for enabled in FEATURES.values())
    assert feature_enabled("ai") is False
    assert feature_enabled("timeline") is False


def test_feature_flagged_route_returns_404_when_disabled():
    client = TestClient(app)
    res = client.get("/api/projects/demo/scenes")
    assert res.status_code == 404


def test_write_atomic_replaces_content(tmp_path):
    path = tmp_path / "chapter.md"
    write_atomic(path, "first\n")
    write_atomic(path, "second\n")
    assert path.read_text(encoding="utf-8") == "second\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_write_markdown_roundtrip_single_frontmatter(tmp_path):
    configure_temp_runtime(tmp_path)
    path = main.NOVELS_ROOT / "demo" / "chapters" / "chapter.md"

    write_markdown(path, {"title": "Chapter 1", "status": "draft"}, "Body")
    raw = path.read_text(encoding="utf-8")
    assert raw.count("---") == 2

    fm, body = read_markdown(path)
    assert fm["title"] == "Chapter 1"
    assert fm["status"] == "draft"
    assert body.strip() == "Body"


def test_write_markdown_persists_synopsis_to_file_index(tmp_path):
    configure_temp_runtime(tmp_path)
    path = main.NOVELS_ROOT / "demo" / "chapters" / "chapter.md"

    write_markdown(
        path,
        {"title": "Chapter", "status": "outline", "synopsis": "One-line outline"},
        "body",
        project="demo",
    )

    with main.get_conn() as conn:
        row = conn.execute(
            "SELECT synopsis FROM file_index WHERE path=?",
            (str(path),),
        ).fetchone()
    assert row["synopsis"] == "One-line outline"


def test_preview_markdown_uses_project_scope_without_name_error(tmp_path):
    configure_temp_runtime(tmp_path)
    with TestClient(app) as client:
        login(client)
        res = client.post("/projects/demo/preview", data={"body": "hello [[Alice]]"})

    assert res.status_code == 200
    assert "hello" in res.text


def test_entity_detail_renders_properties_and_basename_filter(tmp_path):
    configure_temp_runtime(tmp_path)
    chapter = main.NOVELS_ROOT / "demo" / "chapters" / "00001-chapter.md"
    chapter.parent.mkdir(parents=True, exist_ok=True)
    chapter.write_text("---\ntitle: Chapter\n---\nbody", encoding="utf-8")

    with main.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO entities(id, project, kind, name, aliases, md_path, properties, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "ent_abc",
                "demo",
                "character",
                "Alice",
                "[]",
                "",
                '{"age": 18}',
                "now",
                "now",
            ),
        )
        conn.execute(
            "INSERT INTO entity_refs(chapter_path, scene_id, entity_id, ref_kind, char_offset) VALUES (?, ?, ?, ?, ?)",
            (str(chapter), None, "ent_abc", "mention", 7),
        )

    with TestClient(app) as client:
        login(client)
        res = client.get("/projects/demo/entities/ent_abc")

    assert res.status_code == 200
    assert "00001-chapter.md" in res.text
    assert "/arc" not in res.text


def test_export_page_uses_project_name_for_options(tmp_path):
    configure_temp_runtime(tmp_path)
    (main.NOVELS_ROOT / "demo" / "chapters").mkdir(parents=True, exist_ok=True)

    with TestClient(app) as client:
        login(client)
        res = client.get("/export")

    assert res.status_code == 200
    assert 'value="demo"' in res.text


def test_core_project_pages_render_after_ui_cleanup(tmp_path):
    configure_temp_runtime(tmp_path)
    project = "demo"
    chapters_dir = main.NOVELS_ROOT / project / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    main.set_project_meta(project, 100000, "author", "A short synopsis", 2000)
    write_markdown(
        chapters_dir / "00001-start.md",
        {"title": "Start", "chapter": "1", "status": "draft", "synopsis": "Opening beat"},
        "hello world",
        project=project,
    )

    with TestClient(app) as client:
        login(client)
        checks = [
            ("/", "下一步"),
            ("/projects", "新建项目"),
            (f"/projects/{project}", "项目操作"),
            (f"/projects/{project}/stats", "每日字数"),
        ]
        for url, expected in checks:
            res = client.get(url)
            assert res.status_code == 200
            assert expected in res.text
