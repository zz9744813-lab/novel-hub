import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import app.main as main
import app.config as config
import app.db as db
import app.services.markdown_service as markdown_service
import app.services.path_service as path_service
import app.services.snapshot_service as snapshot_service
import app.services.chapter_service as chapter_service
import app.services.library_service as library_service
import app.schema as schema
import app.routers.snapshots as snapshots_router
import app.routers.notes as notes_router
import app.routers.volumes as volumes_router
import app.routers.entities as entities_router
import app.routers.workflow as workflow_router
import app.routers.ai as ai_router
from app.main import app
from app.services.markdown_service import (
    parse_frontmatter,
    write_atomic,
    count_words,
    safe_slug,
    read_markdown,
)
from app.config import FEATURES, feature_enabled
from app.services.chapter_service import write_markdown


def configure_temp_runtime(tmp_path):
    # Patch main.py re-exports
    main.DB_PATH = tmp_path / "novelhub.db"
    main.VAULT_ROOT = tmp_path / "vault"
    main.NOVELS_ROOT = main.VAULT_ROOT / "Novels"
    main.BACKUP_ROOT = main.VAULT_ROOT / ".novelhub-backups"
    main.ADMIN_PASSWORD = "pw"
    main.SECRET_KEY = "test-secret"

    # Patch infrastructure modules
    config.DB_PATH = main.DB_PATH
    db.DB_PATH = main.DB_PATH
    config.VAULT_ROOT = main.VAULT_ROOT
    config.NOVELS_ROOT = main.NOVELS_ROOT
    config.BACKUP_ROOT = main.BACKUP_ROOT
    config.ADMIN_PASSWORD = main.ADMIN_PASSWORD
    config.SECRET_KEY = main.SECRET_KEY

    # Patch services
    markdown_service.VAULT_ROOT = main.VAULT_ROOT
    path_service.VAULT_ROOT = main.VAULT_ROOT
    path_service.NOVELS_ROOT = main.NOVELS_ROOT
    snapshot_service.VAULT_ROOT = main.VAULT_ROOT
    snapshot_service.BACKUP_ROOT = main.BACKUP_ROOT
    chapter_service.VAULT_ROOT = main.VAULT_ROOT
    library_service.NOVELS_ROOT = main.NOVELS_ROOT
    schema.DB_PATH = main.DB_PATH
    snapshots_router.VAULT_ROOT = main.VAULT_ROOT
    snapshots_router.BACKUP_ROOT = main.BACKUP_ROOT
    notes_router.VAULT_ROOT = main.VAULT_ROOT
    volumes_router.NOVELS_ROOT = main.NOVELS_ROOT
    entities_router.NOVELS_ROOT = main.NOVELS_ROOT
    workflow_router.NOVELS_ROOT = main.NOVELS_ROOT
    ai_router.NOVELS_ROOT = main.NOVELS_ROOT

    os.environ["NOVELHUB_DB_PATH"] = str(main.DB_PATH)
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


def test_sqlite_connection_pragmas(tmp_path):
    configure_temp_runtime(tmp_path)

    with main.get_conn() as conn:
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    assert busy_timeout == 5000
    assert foreign_keys == 1


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
    assert 'id="md_content"' in res.text
    assert "实体说明" in res.text
    assert "Entity Description" not in res.text


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


def test_editor_mobile_overlay_and_entity_list_are_chinese(tmp_path):
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

    with TestClient(app) as client:
        login(client)
        editor_res = client.get(f"/projects/{project}/editor/00001-start.md")
        assert editor_res.status_code == 200
        assert "桌面端编辑" in editor_res.text
        assert "Desktop Required" not in editor_res.text
        assert "返回项目" in editor_res.text

        entities_res = client.get(f"/projects/{project}/entities")
        assert entities_res.status_code == 200
        assert "新建实体" in entities_res.text
        assert "New Entity" not in entities_res.text


def test_no_stale_visible_english_copy_in_outline_and_editor_js():
    outline = (Path(__file__).resolve().parent.parent / "app" / "templates" / "outline.html").read_text(encoding="utf-8")
    editor_js = (Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "editor.js").read_text(encoding="utf-8")
    stale_outline = [
        "No synopsis",
        "No POV",
        "Click a chapter",
        "Loading outline",
        "Back to Project",
        "AI Split",
        "Root (No Volume)",
        "Select a volume",
    ]
    stale_editor = [
        "0 words",
        "Delete this scene",
        "Entity not found",
        "View Detail",
        "Has notes",
        "No notes yet",
        "Real Name",
        "Unbound name link",
        "Loading entity info",
        "鏈繚瀛",
    ]
    for text in stale_outline:
        assert text not in outline
    for text in stale_editor:
        assert text not in editor_js


def test_tailwind_uses_local_compiled_css():
    root = Path(__file__).resolve().parent.parent
    base = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    login = (root / "app" / "templates" / "login.html").read_text(encoding="utf-8")
    input_css = (root / "app" / "static" / "css" / "tailwind.input.css").read_text(encoding="utf-8")
    built_css = (root / "app" / "static" / "css" / "tailwind.css")

    assert "cdn.tailwindcss.com" not in base
    assert "cdn.tailwindcss.com" not in login
    assert "/static/css/tailwind.css" in base
    assert "/static/css/tailwind.css" in login
    assert "@tailwind utilities" in input_css
    assert built_css.exists()
    assert ".bg-bg" in built_css.read_text(encoding="utf-8")
    assert "登录 · Novel Hub" in login
    assert "面向长篇小说创作的本地工作台" in login
    assert "Login · Novel Hub" not in login


def test_editor_uses_local_codemirror_bundle():
    root = Path(__file__).resolve().parent.parent
    editor_template = (root / "app" / "templates" / "editor.html").read_text(encoding="utf-8")
    source_js = root / "app" / "static" / "js" / "editor.js"
    bundle_js = root / "app" / "static" / "js" / "editor.bundle.js"

    assert "type=\"importmap\"" not in editor_template
    assert "esm.sh" not in editor_template
    assert "/static/js/editor.bundle.js" in editor_template
    assert source_js.exists()
    assert bundle_js.exists()
    assert "EditorView" in bundle_js.read_text(encoding="utf-8")


def test_ai_context_does_not_import_main_helpers():
    root = Path(__file__).resolve().parent.parent
    ai_context = (root / "app" / "services" / "ai_context.py").read_text(encoding="utf-8")

    assert "from app.main import read_markdown" not in ai_context
