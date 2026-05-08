import gzip
import hashlib
from pathlib import Path
from app.config import VAULT_ROOT
from app.db import get_conn
from app.services.markdown_service import _ensure_under_root, utc_now


def backup_file(path: Path, label: str = "auto") -> None:
    p = _ensure_under_root(path, VAULT_ROOT)
    if not p.exists():
        return
    content = p.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    with get_conn() as conn:
        # Deduplication: check if last snapshot for this file has same hash
        last = conn.execute(
            "SELECT content_hash FROM snapshots WHERE chapter_path = ? ORDER BY created_at DESC LIMIT 1",
            (str(p),)
        ).fetchone()

        if last and last["content_hash"] == content_hash:
            return # No change

        compressed = gzip.compress(content.encode("utf-8"))
        conn.execute(
            "INSERT INTO snapshots(chapter_path, created_at, label, content_hash, content) VALUES (?, ?, ?, ?, ?)",
            (str(p), utc_now().isoformat(), label, content_hash, compressed)
        )
        # Cleanup policy: Keep (Recent 50) UNION (First of each day) UNION (First of each month)
        conn.execute(
            """DELETE FROM snapshots
               WHERE chapter_path = ?
               AND id NOT IN (
                   SELECT id FROM (
                       SELECT id FROM snapshots WHERE chapter_path = ? ORDER BY created_at DESC LIMIT 50
                   )
                   UNION
                   SELECT id FROM (
                       SELECT id FROM snapshots WHERE chapter_path = ? GROUP BY strftime('%Y-%m-%d', created_at)
                   )
                   UNION
                   SELECT id FROM (
                       SELECT id FROM snapshots WHERE chapter_path = ? GROUP BY strftime('%Y-%m', created_at)
                   )
               )""",
            (str(p), str(p), str(p), str(p))
        )
