import os
import sqlite3
import hashlib
import shutil
import argparse
import re
import yaml
from pathlib import Path
from datetime import datetime

# Configuration via Environment Variables
VAULT_ROOT = Path(os.getenv("NOVELHUB_VAULT_ROOT", "f:/hajimi/ObsidianVault")).expanduser()
NOVELS_ROOT = VAULT_ROOT / "Novels"
DB_PATH = Path(os.getenv("NOVELHUB_DB_PATH", str(Path(__file__).resolve().parent.parent / "novelhub.db"))).expanduser()

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_sha1_prefix(text: str, length=8) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]

def parse_frontmatter(content: str):
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return {}, content
    try:
        data = yaml.safe_load(match.group(1)) or {}
        return data, content[match.end():]
    except:
        return {}, content

def migrate(dry_run=True):
    if not NOVELS_ROOT.exists():
        print(f"Error: Novels root {NOVELS_ROOT} not found.")
        return

    conn = get_conn()
    cursor = conn.cursor()

    count = 0
    projects = [p for p in NOVELS_ROOT.iterdir() if p.is_dir()]
    
    for proj_dir in projects:
        project = proj_dir.name
        print(f"Processing project: {project}")
        
        for kind_dir_name in ["characters", "world", "hooks"]:
            kind_path = proj_dir / kind_dir_name
            if not kind_path.exists():
                continue
                
            for md_file in kind_path.glob("*.md"):
                rel_path = md_file.relative_to(NOVELS_ROOT.parent)
                ent_id = f"ent_{get_sha1_prefix(str(rel_path))}"
                name = md_file.stem
                
                content = md_file.read_text(encoding="utf-8")
                fm, _ = parse_frontmatter(content)
                
                # B8/B9: Map kind and properties
                if kind_dir_name == "characters":
                    kind = "character"
                elif kind_dir_name == "hooks":
                    kind = "thread"
                else:
                    # World category mapping
                    kind = fm.get("category", "location").lower()
                    if kind not in ["location", "item", "organization", "concept", "event"]:
                        kind = "location" # Default fallback
                
                # properties is everything except name and category
                props = {k: v for k, v in fm.items() if k not in ["name", "category", "title"]}
                aliases = fm.get("aliases", [])
                if isinstance(aliases, str):
                    aliases = [a.strip() for a in aliases.split(",") if a.strip()]
                
                now = datetime.now().isoformat()
                import json
                
                print(f"  [{kind}] {name} -> {ent_id}")
                
                if not dry_run:
                    cursor.execute(
                        """
                        INSERT INTO entities (id, project, kind, name, aliases, md_path, created_at, updated_at, properties)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            name=excluded.name,
                            kind=excluded.kind,
                            aliases=excluded.aliases,
                            md_path=excluded.md_path,
                            updated_at=excluded.updated_at,
                            properties=excluded.properties
                        """,
                        (ent_id, project, kind, name, json.dumps(aliases), str(md_file), now, now, json.dumps(props))
                    )
                    # Sync to FTS
                    cursor.execute("DELETE FROM entity_fts WHERE id=?", (ent_id,))
                    cursor.execute(
                        "INSERT INTO entity_fts (id, project, name, aliases, properties) VALUES (?, ?, ?, ?, ?)",
                        (ent_id, project, name, json.dumps(aliases), json.dumps(props))
                    )
                count += 1

    if not dry_run:
        conn.commit()
        print(f"Migration complete. {count} entities created/updated.")
    else:
        print(f"Dry run complete. Found {count} entities to migrate.")
    
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate legacy notes to C-Route entities.")
    parser.add_argument("--run", action="store_true", help="Actually perform the migration (default is dry-run)")
    args = parser.parse_args()
    migrate(dry_run=not args.run)
