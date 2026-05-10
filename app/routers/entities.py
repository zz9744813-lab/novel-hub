from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.config import NOVELS_ROOT
from app.db import get_conn
from app.deps import get_templates
from app.security import require_auth
from app.services.markdown_service import read_markdown, safe_slug, utc_now
from app.services.chapter_service import write_markdown

router = APIRouter()


@router.get("/api/entities/{ent_id}/appearances")
def api_get_entity_appearances(request: Request, ent_id: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        appearances = conn.execute("""
            SELECT er.*, fi.title as chapter_title
            FROM entity_refs er
            LEFT JOIN file_index fi ON er.chapter_path = fi.path
            WHERE er.entity_id = ?
        """, (ent_id,)).fetchall()
        return JSONResponse(content={"status": "ok", "appearances": [dict(a) for a in appearances]})


@router.get("/api/entities")
def api_list_entities(request: Request, project: str, kind: str = None, q: str = None) -> Response:
    require_auth(request)
    with get_conn() as conn:
        query = "SELECT * FROM entities WHERE project = ?"
        params: list = [project]
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        if q:
            query += " AND (name LIKE ? OR aliases LIKE ?)"
            params.append(f"%{q}%")
            params.append(f"%{q}%")
        rows = conn.execute(query, params).fetchall()
        return JSONResponse(content={"status": "ok", "entities": [dict(r) for r in rows]})

@router.post("/api/entities")
async def api_create_entity(request: Request) -> Response:
    require_auth(request)
    data = await request.json()
    project = data.get("project")
    kind = data.get("kind")
    name = data.get("name")
    if not project or not name:
        raise HTTPException(400, "project and name required")

    ent_id = data.get("id") or f"ent_{hashlib.sha1((project + name + str(utc_now())).encode()).hexdigest()[:8]}"
    now = utc_now().isoformat()

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO entities (id, project, kind, name, aliases, properties, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ent_id, project, kind, name, json.dumps(data.get("aliases", [])), json.dumps(data.get("properties", {})), now, now)
        )
        conn.execute(
            "INSERT INTO entity_fts (id, project, name, aliases, properties) VALUES (?, ?, ?, ?, ?)",
            (ent_id, project, name, json.dumps(data.get("aliases", [])), json.dumps(data.get("properties", {})))
        )
    return JSONResponse(content={"status": "ok", "id": ent_id})


@router.put("/api/entities/{ent_id}")
async def api_update_entity(request: Request, ent_id: str) -> Response:
    require_auth(request)
    data = await request.json()
    with get_conn() as conn:
        old = conn.execute("SELECT * FROM entities WHERE id=?", (ent_id,)).fetchone()
        if not old:
            raise HTTPException(404)

        new_name = data["name"]
        new_aliases = json.dumps(data.get("aliases", []))
        new_props = json.dumps(data.get("properties", {}))
        now = utc_now().isoformat()

        # Diff & log property changes
        try:
            old_props = json.loads(old["properties"] or "{}")
        except Exception:
            old_props = {}
        new_props_dict = data.get("properties", {})
        for k in set(old_props.keys()) | set(new_props_dict.keys()):
            if old_props.get(k) != new_props_dict.get(k):
                conn.execute(
                    """INSERT INTO entity_history(entity_id, project, chapter_int, field, old_value, new_value, changed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (ent_id, old["project"], None, k,
                     json.dumps(old_props.get(k)) if old_props.get(k) is not None else None,
                     json.dumps(new_props_dict.get(k)) if new_props_dict.get(k) is not None else None,
                     now)
                )
        if old["name"] != new_name:
            conn.execute(
                """INSERT INTO entity_history(entity_id, project, chapter_int, field, old_value, new_value, changed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ent_id, old["project"], None, "name", old["name"], new_name, now)
            )

        conn.execute(
            "UPDATE entities SET name=?, aliases=?, properties=?, updated_at=? WHERE id=?",
            (new_name, new_aliases, new_props, now, ent_id)
        )
        conn.execute("DELETE FROM entity_fts WHERE id=?", (ent_id,))
        conn.execute(
            "INSERT INTO entity_fts (id, project, name, aliases, properties) VALUES (?, ?, ?, ?, ?)",
            (ent_id, old["project"], new_name, new_aliases, new_props)
        )

        # Save markdown body to disk if md_path exists
        md_content = data.get("md_content")
        if md_content is not None and old["md_path"]:
            md_path = Path(old["md_path"])
            if md_path.exists():
                fm, _ = read_markdown(md_path)
                fm["title"] = new_name
                write_markdown(md_path, fm, md_content, project=old["project"])

        # Cascade rename (rewrite display_text in [[ent_xxx|old_name]])
        if data.get("cascade") and old["name"] != new_name:
            chapters_dir = NOVELS_ROOT / old["project"] / "chapters"
            pattern = re.compile(r"\[\[" + re.escape(ent_id) + r"\|[^\[\]]*?\]\]")
            for f in chapters_dir.rglob("*.md") if chapters_dir.exists() else []:
                try:
                    text = f.read_text(encoding="utf-8")
                    new_text = pattern.sub(f"[[{ent_id}|{new_name}]]", text)
                    if new_text != text:
                        f.write_text(new_text, encoding="utf-8")
                except Exception:
                    pass

    return JSONResponse(content={"status": "ok"})


@router.delete("/api/entities/{ent_id}")
def api_delete_entity(request: Request, ent_id: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        conn.execute("DELETE FROM entities WHERE id=?", (ent_id,))
        conn.execute("DELETE FROM entity_fts WHERE id=?", (ent_id,))
        conn.execute("DELETE FROM entity_relations WHERE source_id=? OR target_id=?", (ent_id, ent_id))
        conn.execute("DELETE FROM entity_refs WHERE entity_id=?", (ent_id,))
    return JSONResponse(content={"status": "ok"})


@router.get("/api/entities/{ent_id}")
def api_get_entity(request: Request, ent_id: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        entity = conn.execute("SELECT * FROM entities WHERE id = ?", (ent_id,)).fetchone()
        if not entity:
            raise HTTPException(404)
        relations = conn.execute(
            """SELECT er.*, e.name as target_name FROM entity_relations er
               JOIN entities e ON er.target_id = e.id
               WHERE source_id = ?""", (ent_id,)).fetchall()
        appearances = conn.execute("SELECT * FROM entity_refs WHERE entity_id = ?", (ent_id,)).fetchall()
        return JSONResponse(content={
            "status": "ok",
            "entity": dict(entity),
            "relations": [dict(r) for r in relations],
            "appearances": [dict(a) for a in appearances]
        })

@router.post("/api/entity-relations")
async def api_create_relation(request: Request) -> Response:
    require_auth(request)
    data = await request.json()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO entity_relations (project, source_id, target_id, relation_type, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (data["project"], data["source_id"], data["target_id"], data["relation_type"], data.get("notes", ""), utc_now().isoformat())
        )
    return JSONResponse(content={"status": "ok"})


@router.delete("/api/entity-relations/{rel_id}")
def api_delete_relation(request: Request, rel_id: int) -> Response:
    require_auth(request)
    with get_conn() as conn:
        conn.execute("DELETE FROM entity_relations WHERE id = ?", (rel_id,))
    return JSONResponse(content={"status": "ok"})


@router.get("/api/entity-relations")
def api_list_relations(request: Request, project: str) -> Response:
    require_auth(request)
    with get_conn() as conn:
        relations = conn.execute("SELECT * FROM entity_relations WHERE project = ?", (project,)).fetchall()
        return JSONResponse(content={"status": "ok", "relations": [dict(r) for r in relations]})


@router.post("/api/projects/{project}/bulk-bind-entities")
async def api_bulk_bind_entities(request: Request, project: str) -> Response:
    require_auth(request)
    safe_project = safe_slug(project, fallback="project")
    data = await request.json()
    only_for = data.get("entity_id")

    chapters_dir = NOVELS_ROOT / safe_project / "chapters"
    if not chapters_dir.exists():
        return JSONResponse({"status": "ok", "updated": 0})

    with get_conn() as conn:
        if only_for:
            ent = conn.execute(
                "SELECT id, name, aliases FROM entities WHERE id=? AND project=?",
                (only_for, safe_project)
            ).fetchone()
            if not ent:
                raise HTTPException(404, "entity not found")
            entities = [ent]
        else:
            entities = conn.execute(
                "SELECT id, name, aliases FROM entities WHERE project=?",
                (safe_project,)
            ).fetchall()

    name_map: dict[str, str] = {}
    for e in entities:
        name_map[e["name"]] = e["id"]
        try:
            aliases = json.loads(e["aliases"] or "[]")
            for a in aliases:
                name_map[a] = e["id"]
        except Exception:
            pass

    updated = 0
    pattern = re.compile(r"\[\[([^\[\]|#]+?)\]\]")

    for f in chapters_dir.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8")
            new_text = pattern.sub(lambda m:
                f"[[{name_map[m.group(1).strip()]}|{m.group(1).strip()}]]"
                if m.group(1).strip() in name_map else m.group(0),
                text
            )
            if new_text != text:
                f.write_text(new_text, encoding="utf-8")
                fm, body = read_markdown(f)
                write_markdown(f, fm, body, project=safe_project)
                updated += 1
        except Exception as e:
            print(f"bulk-bind error on {f}: {e}")

    return JSONResponse({"status": "ok", "updated": updated})


# ===== Page routes =====

@router.get("/projects/{project}/entities", response_class=HTMLResponse)
def entities_page(request: Request, project: str, kind: str = None) -> Response:
    require_auth(request)
    templates = get_templates()
    with get_conn() as conn:
        query = "SELECT * FROM entities WHERE project = ?"
        params: list = [project]
        if kind:
            if kind == "world":  # legacy mapping
                query += " AND kind NOT IN ('character', 'thread')"
            else:
                query += " AND kind = ?"
                params.append(kind)
        entities = conn.execute(query, params).fetchall()
        return templates.TemplateResponse(
            "entities_list.html",
            {
                "request": request,
                "project": project,
                "entities": [dict(e) for e in entities],
                "kind": kind
            }
        )


@router.get("/projects/{project}/entities/{ent_id}", response_class=HTMLResponse)
def entity_detail_page(request: Request, project: str, ent_id: str) -> Response:
    require_auth(request)
    templates = get_templates()
    with get_conn() as conn:
        entity = conn.execute("SELECT * FROM entities WHERE id = ?", (ent_id,)).fetchone()
        if not entity:
            raise HTTPException(404)

        # Load markdown content from md_path if exists
        md_content = ""
        if entity["md_path"] and Path(entity["md_path"]).exists():
            fm, md_content = read_markdown(Path(entity["md_path"]))

        relations = conn.execute(
            """SELECT er.*, e.name as target_name FROM entity_relations er
               JOIN entities e ON er.target_id = e.id
               WHERE source_id = ?""", (ent_id,)).fetchall()
        appearances = conn.execute("SELECT * FROM entity_refs WHERE entity_id = ?", (ent_id,)).fetchall()
        try:
            entity_properties = json.loads(entity["properties"] or "{}")
        except json.JSONDecodeError:
            entity_properties = {}

        return templates.TemplateResponse(
            "entity_detail.html",
            {
                "request": request,
                "project": project,
                "entity": dict(entity),
                "entity_aliases": json.loads(entity["aliases"] or "[]"),
                "entity_properties": entity_properties,
                "md_content": md_content,
                "relations": [dict(r) for r in relations],
                "appearances": [dict(a) for a in appearances]
            }
        )
