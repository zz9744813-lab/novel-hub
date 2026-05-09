from __future__ import annotations

import difflib
import gzip
import hashlib
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.config import BACKUP_ROOT, VAULT_ROOT
from app.db import get_conn
from app.deps import get_templates
from app.security import require_auth
from app.services.chapter_service import write_markdown
from app.services.markdown_service import read_markdown, safe_slug, utc_now
from app.services.metrics_service import log_operation
from app.services.path_service import chapter_path
from app.services.snapshot_service import backup_file

router = APIRouter()


@router.get("/projects/{project}/backups/{filename}")
def list_backups(request: Request, project: str, filename: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    p = chapter_path(safe_project, filename)
    rel = p.relative_to(VAULT_ROOT)
    dest_dir = BACKUP_ROOT / rel.parent

    backups = []
    if dest_dir.exists():
        for b in sorted(
            dest_dir.glob(f"*__{filename}"), key=lambda x: x.name, reverse=True
        ):
            timestamp = b.name.split("__")[0]
            backups.append(
                {"name": b.name, "timestamp": timestamp, "size": b.stat().st_size}
            )

    return JSONResponse(content={"backups": backups})


@router.get("/projects/{project}/diff/{backup_name}")
def view_diff(request: Request, project: str, backup_name: str) -> Response:
    require_auth(request)
    # Extract original filename from backup_name (format: YYYYMMDDTHHMMSSZ__filename.md)
    if "__" not in backup_name:
        raise HTTPException(400)

    filename = backup_name.split("__", 1)[1]
    safe_project = safe_slug(project, fallback="project")
    current_p = chapter_path(safe_project, filename)

    # Locate backup file
    rel = current_p.relative_to(VAULT_ROOT)
    backup_p = BACKUP_ROOT / rel.parent / backup_name

    if not current_p.exists() or not backup_p.exists():
        raise HTTPException(404)

    current_text = current_p.read_text(encoding="utf-8").splitlines()
    backup_text = backup_p.read_text(encoding="utf-8").splitlines()

    diff = list(
        difflib.unified_diff(backup_text, current_text, fromfile="备份", tofile="当前")
    )
    templates = get_templates()
    return templates.TemplateResponse("_diff.html", {"request": request, "diff": diff})


@router.post("/api/snapshots/{snap_id}/restore")
def restore_snapshot(request: Request, snap_id: int) -> Response:
    require_auth(request)
    with get_conn() as conn:
        snap = conn.execute(
            "SELECT * FROM snapshots WHERE id = ?", (snap_id,)
        ).fetchone()
        if not snap:
            raise HTTPException(404, "Snapshot not found")

        content = gzip.decompress(snap["content"]).decode("utf-8")
        path = Path(snap["chapter_path"])

        # Backup current state as snapshot before overwriting
        backup_file(path, label="pre-restore")

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        # Re-index
        fm, body = read_markdown(path)
        write_markdown(path, fm, body)

        return JSONResponse(content={"status": "ok"})


@router.post("/projects/{project}/backups/{backup_name}/restore")
def restore_backup(request: Request, project: str, backup_name: str) -> Response:
    require_auth(request)
    if "__" not in backup_name:
        raise HTTPException(400)
    filename = backup_name.split("__", 1)[1]
    safe_project = safe_slug(project, fallback="project")
    current_p = chapter_path(safe_project, filename)
    rel = current_p.relative_to(VAULT_ROOT)
    backup_p = BACKUP_ROOT / rel.parent / backup_name
    if not backup_p.exists():
        raise HTTPException(404)
    # Backup current before restoring
    backup_file(current_p)
    shutil.copy2(backup_p, current_p)
    # Reload and index
    fm, body = read_markdown(current_p)
    write_markdown(current_p, fm, body)
    log_operation(
        "restore_backup", str(current_p), backup_name, project=safe_project
    )
    return JSONResponse(content={"status": "ok"})


@router.post("/api/chapters/snapshot")
async def manual_snapshot(request: Request) -> Response:
    require_auth(request)
    data = await request.json()
    project = data.get("project")
    filename = data.get("filename")
    label = data.get("label", "manual")

    safe_project = safe_slug(project)
    path = chapter_path(safe_project, filename)
    if not path.exists():
        raise HTTPException(404)

    content = path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    compressed = gzip.compress(content.encode("utf-8"))

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO snapshots(chapter_path, created_at, label, content_hash, content, protected) VALUES (?, ?, ?, ?, ?, ?)",
            (str(path), utc_now().isoformat(), label, content_hash, compressed, 1),
        )
    return JSONResponse({"status": "ok"})


@router.get("/projects/{project}/snapshots/{snap_id}/diff")
def view_snapshot_diff(request: Request, project: str, snap_id: int) -> Response:
    require_auth(request)
    with get_conn() as conn:
        snap = conn.execute(
            "SELECT * FROM snapshots WHERE id = ?", (snap_id,)
        ).fetchone()
        if not snap:
            raise HTTPException(404)

    path = Path(snap["chapter_path"])
    current_text = (
        path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    )
    backup_text = gzip.decompress(snap["content"]).decode("utf-8").splitlines()

    diff = list(
        difflib.unified_diff(
            backup_text,
            current_text,
            fromfile=f"Snapshot ({snap['label']})",
            tofile="Current",
        )
    )
    templates = get_templates()
    return templates.TemplateResponse(
        "_diff.html", {"request": request, "diff": diff, "snap_id": snap_id}
    )


@router.post("/api/projects/{project}/snapshots")
async def api_create_snapshot(request: Request, project: str) -> Response:
    require_auth(request)
    data = await request.json()
    path_str = data.get("path")
    label = data.get("label", "manual")
    if not path_str:
        raise HTTPException(400, "path required")

    path = Path(path_str)
    if not path.exists():
        raise HTTPException(404, "file not found")

    backup_file(path, label=label)
    return JSONResponse(content={"status": "ok"})


@router.get("/api/snapshots/{snap_id}/diff")
def api_snapshot_diff(request: Request, snap_id: int) -> Response:
    require_auth(request)
    with get_conn() as conn:
        snap = conn.execute(
            "SELECT * FROM snapshots WHERE id=?", (snap_id,)
        ).fetchone()
        if not snap:
            raise HTTPException(404)
        old_content = gzip.decompress(snap["content"]).decode("utf-8")
    path = Path(snap["chapter_path"])
    if not path.exists():
        raise HTTPException(404, "current file gone")
    new_content = path.read_text(encoding="utf-8")

    diff = list(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"snapshot {snap['created_at']}",
            tofile="current",
            n=3,
        )
    )
    return JSONResponse(
        {
            "status": "ok",
            "diff": "".join(diff),
            "old_lines": len(old_content.splitlines()),
            "new_lines": len(new_content.splitlines()),
        }
    )


@router.put("/api/snapshots/{snap_id}")
async def api_update_snapshot(request: Request, snap_id: int) -> Response:
    """Set label or protected flag on a snapshot."""
    require_auth(request)
    data = await request.json()
    fields = []
    params = []
    if "label" in data:
        fields.append("label=?")
        params.append(data["label"])
    if "protected" in data:
        fields.append("protected=?")
        params.append(1 if data["protected"] else 0)
    if not fields:
        return JSONResponse({"status": "ok"})
    params.append(snap_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE snapshots SET {','.join(fields)} WHERE id=?", params)
    return JSONResponse({"status": "ok"})
