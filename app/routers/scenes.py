from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.config import require_feature
from app.db import get_conn
from app.security import require_auth
from app.services.markdown_service import utc_now

router = APIRouter()


@router.get("/api/projects/{project}/scenes")
def api_list_scenes(request: Request, project: str, chapter: str = None) -> Response:
    require_feature("scenes")
    require_auth(request)
    with get_conn() as conn:
        query = "SELECT * FROM scenes WHERE project = ?"
        params = [project]
        if chapter:
            query += " AND chapter_path LIKE ?"
            params.append(f"%{chapter}")
        query += " ORDER BY chapter_path, seq"
        rows = conn.execute(query, params).fetchall()
        return JSONResponse(content={"status": "ok", "scenes": [dict(r) for r in rows]})


@router.post("/api/projects/{project}/scenes")
async def api_create_scene(request: Request, project: str) -> Response:
    require_feature("scenes")
    require_auth(request)
    data = await request.json()
    sc_id = f"sc_{hashlib.sha1((project + data['chapter_path'] + str(utc_now())).encode()).hexdigest()[:8]}"
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO scenes (id, chapter_path, project, seq, title, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sc_id, data["chapter_path"], project, data.get("seq", 0), data.get("title", "New Scene"), "draft")
        )
    return JSONResponse(content={"status": "ok", "id": sc_id})


@router.put("/api/scenes/{sc_id}")
async def api_update_scene(request: Request, sc_id: str) -> Response:
    require_feature("scenes")
    require_auth(request)
    data = await request.json()
    with get_conn() as conn:
        conn.execute(
            """UPDATE scenes SET
               title=?, pov=?, location_id=?, summary=?, status=?
               WHERE id=?""",
            (data.get("title"), data.get("pov"), data.get("location_id"), data.get("summary"), data.get("status"), sc_id)
        )
    return JSONResponse(content={"status": "ok"})


@router.delete("/api/scenes/{sc_id}")
def api_delete_scene(request: Request, sc_id: str) -> Response:
    require_feature("scenes")
    require_auth(request)
    with get_conn() as conn:
        conn.execute("DELETE FROM scenes WHERE id=?", (sc_id,))
    return JSONResponse(content={"status": "ok"})


@router.get("/api/projects/{project}/outline")
def api_get_outline(request: Request, project: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        # Simple tree: Volumes -> Chapters -> Scenes
        volumes = conn.execute("SELECT * FROM volumes WHERE project = ? ORDER BY seq", (project,)).fetchall()
        chapters = conn.execute("SELECT * FROM file_index WHERE project = ? ORDER BY volume, chapter_int", (project,)).fetchall()
        scenes = conn.execute("SELECT * FROM scenes WHERE project = ? ORDER BY chapter_path, seq", (project,)).fetchall()

        return JSONResponse(content={
            "status": "ok",
            "volumes": [dict(v) for v in volumes],
            "chapters": [dict(c) for c in chapters],
            "scenes": [dict(s) for s in scenes]
        })
