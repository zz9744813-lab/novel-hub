import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import count_words, parse_frontmatter, safe_slug


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
