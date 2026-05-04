import json
import sqlite3
from pathlib import Path
from typing import Dict, Any, List
import re



def build_context(project: str, chapter_path: str = None, mode: str = "continue") -> str:
    """
    Builds a rich context prompt for AI generation.
    """
    from app.main import get_conn
    with get_conn() as conn:
        context_parts = []
    
        # 1. Project Level
        proj_meta = conn.execute("SELECT * FROM project_meta WHERE project = ?", (project,)).fetchone()
        if proj_meta:
            context_parts.append(f"### Project: {project}")
            context_parts.append(f"Synopsis: {proj_meta['synopsis']}")
            context_parts.append(f"Author Style: {proj_meta['author']}")
    
        # 2. Main Characters (Top 5)
        chars = conn.execute(
            "SELECT name, properties FROM entities WHERE project = ? AND kind = 'character' LIMIT 5",
            (project,)
        ).fetchall()
        if chars:
            context_parts.append("### Key Characters:")
            for c in chars:
                props = json.loads(c['properties'] or '{}')
                desc = f"{c['name']}: {props.get('appearance', '')} {props.get('personality', '')}".strip()
                context_parts.append(f"- {desc}")
    
        # 3. Chapter / Volume Level
        if chapter_path:
            curr_ch = conn.execute("SELECT * FROM file_index WHERE path = ?", (chapter_path,)).fetchone()
            if curr_ch:
                # Volume synopsis
                if curr_ch['volume']:
                    vol = conn.execute(
                        "SELECT synopsis FROM volumes WHERE project = ? AND slug = ?",
                        (project, curr_ch['volume'])
                    ).fetchone()
                    if vol and vol['synopsis']:
                        context_parts.append(f"### Current Volume ({curr_ch['volume']}):\n{vol['synopsis']}")
                
                context_parts.append(f"### Current Chapter Synopsis:\n{curr_ch['synopsis']}")
    
                # Previous chapter context
                prev_ch = conn.execute(
                    "SELECT path FROM file_index WHERE project = ? AND chapter_int < ? ORDER BY chapter_int DESC LIMIT 1",
                    (project, curr_ch['chapter_int'])
                ).fetchone()
                if prev_ch and Path(prev_ch['path']).exists():
                    from app.main import read_markdown # Import inside to avoid circular deps if possible
                    _, body = read_markdown(Path(prev_ch['path']))
                    tail = body.strip()[-500:]
                    context_parts.append(f"### Previous Chapter Ending:\n...{tail}")
    
                # Mentioned Entities in this chapter
                refs = conn.execute("""
                    SELECT e.name, e.properties 
                    FROM entity_refs er
                    JOIN entities e ON er.entity_id = e.id
                    WHERE er.chapter_path = ?
                """, (chapter_path,)).fetchall()
                if refs:
                    context_parts.append("### Entities in this Chapter:")
                    for r in refs:
                        p = json.loads(r['properties'] or '{}')
                        context_parts.append(f"- {r['name']}: {json.dumps(p)}")
    
        # 4. Style Samples (2 random done chapters)
        samples = conn.execute(
            "SELECT path FROM file_index WHERE project = ? AND status = 'done' ORDER BY RANDOM() LIMIT 2",
            (project,)
        ).fetchall()
        if samples:
            context_parts.append("### Style Samples:")
            from app.main import read_markdown
            for s in samples:
                if Path(s['path']).exists():
                    _, body = read_markdown(Path(s['path']))
                    context_parts.append(f"Sample:\n{body[:300]}...")
    
    return "\n\n".join(context_parts)
