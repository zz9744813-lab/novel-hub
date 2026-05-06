import re
import os
from typing import List, Tuple, Dict, Any
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "novelhub.db"

WIKI_LINK_PATTERN = re.compile(r"\[\[(.*?)\]\]")

def get_conn():
    db_path = Path(os.getenv("NOVELHUB_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def parse_wiki_links(content: str, project: str, conn=None) -> List[Dict[str, Any]]:
    """
    Parses wiki links from content and resolves them to entity IDs.
    Returns a list of dicts: {entity_id, offset, ref_kind, display_text, is_ambiguous}
    """
    results = []
    own_conn = False
    if conn is None:
        conn = get_conn()
        own_conn = True
    
    try:
        for match in WIKI_LINK_PATTERN.finditer(content):
            raw_text = match.group(1)
            offset = match.start()
            
            if "|" in raw_text:
                target, display_text = raw_text.split("|", 1)
            else:
                target = raw_text
                display_text = raw_text
                
            anchor = None
            if "#" in target:
                target, anchor = target.split("#", 1)
                
            entity_id = None
            is_ambiguous = False
            
            if target.startswith("ent_"):
                entity_id = target
            else:
                # B11: Better matching for names/aliases
                # Exact name or exact match in JSON array
                # SQLite 3.38+ supports json_each
                rows = conn.execute(
                    """
                    SELECT e.id FROM entities e
                    WHERE e.project = ? AND (
                        e.name = ? OR 
                        EXISTS (SELECT 1 FROM json_each(e.aliases) WHERE value = ?)
                    )
                    """,
                    (project, target, target)
                ).fetchall()
                
                if len(rows) == 1:
                    entity_id = rows[0]["id"]
                elif len(rows) > 1:
                    entity_id = rows[0]["id"]
                    is_ambiguous = True
            
            # B13: Resolve current entity name
            if entity_id:
                ent_row = conn.execute("SELECT name FROM entities WHERE id = ?", (entity_id,)).fetchone()
                if ent_row:
                    display_text = ent_row["name"]
            results.append({
                "entity_id": entity_id,
                "offset": offset,
                "ref_kind": "wiki",
                "display_text": display_text,
                "anchor": anchor,
                "is_ambiguous": is_ambiguous,
                "raw": match.group(0)
            })
    finally:
        if own_conn:
            conn.close()
            
    return results

def update_entity_refs(chapter_path: str, content: str, project: str):
    """
    Parses links and updates entity_refs table.
    """
    links = parse_wiki_links(content, project)
    conn = get_conn()
    
    # Delete old refs for this chapter
    conn.execute("DELETE FROM entity_refs WHERE chapter_path = ?", (chapter_path,))
    
    for link in links:
        if link["entity_id"]:
            conn.execute(
                """
                INSERT INTO entity_refs (chapter_path, entity_id, ref_kind, char_offset)
                VALUES (?, ?, ?, ?)
                """,
                (chapter_path, link["entity_id"], link["ref_kind"], link["offset"])
            )
            
    conn.commit()
    conn.close()
