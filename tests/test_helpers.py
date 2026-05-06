import sys
import os
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


def test_safe_slug_basic():
    assert safe_slug("Hello World") == "hello-world"
    assert safe_slug("foo/../bar") == "foobar"


def test_safe_slug_empty():
    assert safe_slug("", fallback="x") == "x"


def test_safe_slug_chinese():
    assert safe_slug("中文项目") == "中文项目"


def test_count_words_latin():
    assert count_words("hello world") == 2


def test_count_words_cjk():
    assert count_words("你好世界") == 4


def test_count_words_mixed():
    assert count_words("混合 hello 文字 123") == 4 + 2


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
    res = client.get("/api/projects/demo/timeline")
    assert res.status_code == 404


def test_write_atomic_replaces_content(tmp_path):
    path = tmp_path / "chapter.md"
    write_atomic(path, "first\n")
    write_atomic(path, "second\n")
    assert path.read_text(encoding="utf-8") == "second\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_write_markdown_roundtrip_single_frontmatter(tmp_path):
    main.DB_PATH = tmp_path / "novelhub.db"
    os.environ["NOVELHUB_DB_PATH"] = str(main.DB_PATH)
    main.VAULT_ROOT = tmp_path / "vault"
    main.NOVELS_ROOT = main.VAULT_ROOT / "Novels"
    main.BACKUP_ROOT = main.VAULT_ROOT / ".novelhub-backups"
    main.init_db()

    path = main.NOVELS_ROOT / "demo" / "chapters" / "chapter.md"
    write_markdown(path, {"title": "第一章", "status": "draft"}, "正文")
    raw = path.read_text(encoding="utf-8")
    assert raw.count("---") == 2

    fm, body = read_markdown(path)
    assert fm["title"] == "第一章"
    assert fm["status"] == "draft"
    assert body.strip() == "正文"
