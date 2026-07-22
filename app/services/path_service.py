from pathlib import Path
from app.config import NOVELS_ROOT, VAULT_ROOT
from app.services.markdown_service import safe_slug, _ensure_under_root


def chapter_path(project: str, filename: str, volume: str = None) -> Path:
    safe_project = safe_slug(project, fallback="project")
    safe_file = safe_slug(filename.replace(".md", ""), fallback="chapter") + ".md"
    base_folder = NOVELS_ROOT / safe_project / "chapters"

    if volume is not None:
        vol_folder = safe_slug(volume, fallback="volume-01") if volume else "volume-01"
        return _ensure_under_root(base_folder / vol_folder / safe_file, VAULT_ROOT)

    for f in base_folder.rglob(safe_file):
        return _ensure_under_root(f, VAULT_ROOT)

    return _ensure_under_root(base_folder / "volume-01" / safe_file, VAULT_ROOT)


def project_path(project: str) -> Path:
    path = NOVELS_ROOT / safe_slug(project, fallback="project")
    return _ensure_under_root(path, VAULT_ROOT)


def list_markdown_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    if folder.name == "chapters":
        return sorted(folder.rglob("*.md"))
    return sorted(folder.glob("*.md"))
