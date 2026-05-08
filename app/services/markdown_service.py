import os
import re
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from fastapi import HTTPException
from app.config import VAULT_ROOT

FRONTMATTER_PATTERN = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_slug(value: str, fallback: str = "untitled") -> str:
    value = re.sub(r"[\\/\.]+", "", value.strip())
    cleaned = re.sub(r"[^A-Za-z0-9\-_\u4e00-\u9fff]+", "-", value).lower()
    cleaned = cleaned.strip("-")
    return cleaned or fallback


def _ensure_under_root(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise HTTPException(status_code=400, detail="invalid path")
    return resolved


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return {}, content
    data = yaml.safe_load(match.group(1)) or {}
    body = content[match.end() :]
    return data, body


def dump_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    frontmatter_clean = {k: v for k, v in frontmatter.items() if v not in (None, "", [])}
    fm = yaml.safe_dump(frontmatter_clean, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{body}"


def read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    p = _ensure_under_root(path, VAULT_ROOT)
    if not p.exists():
        return {}, ""
    content = p.read_text(encoding="utf-8")
    return parse_frontmatter(content)


def write_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def count_words(text: str) -> int:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z0-9_]+", text))
    return cjk + latin


def parse_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _project_from_path(p: Path) -> str:
    """Walk path parts looking for the 'Novels' segment; project is whatever follows it."""
    try:
        idx = p.parts.index("Novels")
        return p.parts[idx + 1] if idx + 1 < len(p.parts) else ""
    except ValueError:
        return ""
