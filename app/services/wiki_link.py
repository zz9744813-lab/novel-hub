import re
from typing import List, Tuple, Dict, Any
import sqlite3
from pathlib import Path

# Assuming the database path is consistent with the main app
DB_PATH = Path(__file__).resolve().parent.parent.parent / "novelhub.db"

WIKI_LINK_PATTERN = re.compile(r"\[\[(.*?)\]\]")

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def parse_wiki_links(content: str, project: str) -> List[Dict[str, Any]]:
    """
    Parses wiki links from content and resolves them to entity IDs.
    Returns a list of dicts: {entity_id, offset, ref_kind, display_text, is_ambiguous}
    """
    results = []
    conn = get_conn()
    
    for match in WIKI_LINK_PATTERN.finditer(content):
        raw_text = match.group(1)
        offset = match.start()
        
        # 1. Split display text if present [[target|display]]
        if "|" in raw_text:
            target, display_text = raw_text.split("|", 1)
        else:
            target = raw_text
            display_text = raw_text
            
        # 2. Handle anchor [[target#anchor]]
        anchor = None
        if "#" in target:
            target, anchor = target.split("#", 1)
            
        entity_id = None
        is_ambiguous = False
        
        # 3. Resolve target
        if target.startswith("ent_"):
            # Direct ID link
            entity_id = target
        else:
            # Name or Alias lookup
            # We search for exact name or alias within the project
            rows = conn.execute(
                """
                SELECT id FROM entities 
                WHERE project = ? AND (name = ? OR aliases LIKE ?)
                """,
                (project, target, f"%{target}%")
            ).fetchall()
            
            if len(rows) == 1:
                entity_id = rows[0]["id"]
            elif len(rows) > 1:
                entity_id = rows[0]["id"] # Take first as placeholder
                is_ambiguous = True
        
        results.append({
            "entity_id": entity_id,
            "offset": offset,
            "ref_kind": "wiki", # default kind
            "display_text": display_text,
            "anchor": anchor,
            "is_ambiguous": is_ambiguous,
            "raw": match.group(0)
        })
        
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
