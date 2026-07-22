from __future__ import annotations

import json
from typing import Any

from app.db import get_conn


def get_project_graph(project: str, kind: str | None = None, q: str | None = None) -> dict[str, list[dict[str, Any]]]:
    nodes = []
    links = []

    with get_conn() as conn:
        # Build node query
        node_query = "SELECT id, name, kind, aliases FROM entities WHERE project = ?"
        node_params = [project]

        if kind:
            node_query += " AND kind = ?"
            node_params.append(kind)

        if q:
            node_query += " AND (name LIKE ? OR aliases LIKE ?)"
            node_params.append(f"%{q}%")
            node_params.append(f"%{q}%")

        entity_rows = conn.execute(node_query, node_params).fetchall()

        # Build map of entity ref counts
        ref_rows = conn.execute(
            "SELECT entity_id, COUNT(*) as cnt FROM entity_refs GROUP BY entity_id"
        ).fetchall()
        ref_counts = {row["entity_id"]: row["cnt"] for row in ref_rows}

        # First pass to build a fast lookup for node inclusion
        included_node_ids = {row["id"] for row in entity_rows}

        # Query links
        link_rows = conn.execute(
            "SELECT id, source_id, target_id, relation_type, notes FROM entity_relations WHERE project = ?",
            (project,)
        ).fetchall()

        # Filter links and count degrees
        degree_counts = {}
        for r in link_rows:
            source = r["source_id"]
            target = r["target_id"]
            if source in included_node_ids and target in included_node_ids:
                links.append({
                    "id": r["id"],
                    "source": source,
                    "target": target,
                    "relation_type": r["relation_type"],
                    "notes": r["notes"]
                })
                degree_counts[source] = degree_counts.get(source, 0) + 1
                degree_counts[target] = degree_counts.get(target, 0) + 1

        # Finalize nodes
        for row in entity_rows:
            ent_id = row["id"]
            aliases_str = row["aliases"]
            try:
                aliases = json.loads(aliases_str) if aliases_str else []
                if not isinstance(aliases, list):
                    aliases = []
            except Exception:
                aliases = []

            nodes.append({
                "id": ent_id,
                "name": row["name"],
                "kind": row["kind"],
                "aliases": aliases,
                "degree": degree_counts.get(ent_id, 0),
                "ref_count": ref_counts.get(ent_id, 0),
            })

    return {
        "nodes": nodes,
        "links": links
    }
