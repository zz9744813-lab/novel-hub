import os
import sqlite3
import hashlib
import shutil
import argparse
from pathlib import Path
from datetime import datetime

# Configuration
VAULT_ROOT = Path("/root/ObsidianVault") # Should be replaced by actual path if needed
if not VAULT_ROOT.exists():
    # Attempt to find it relative to script or use env
    VAULT_ROOT = Path("f:/hajimi/ObsidianVault") # Default for this workspace if applicable

# In this specific environment, I'll use the path from the user's workspace
WORKSPACE_ROOT = Path("f:/hajimi/novel-hub")
DB_PATH = WORKSPACE_ROOT / "novelhub.db"
NOVELS_ROOT = Path("f:/hajimi/ObsidianVault/Novels") # Adjusting to user's likely path

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_sha1_prefix(text: str, length=8) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]

def migrate(dry_run=True):
    if not NOVELS_ROOT.exists():
        print(f"Error: Novels root {NOVELS_ROOT} not found.")
        return

    # Backup
    if not dry_run:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = WORKSPACE_ROOT / ".pre-c-migration" / timestamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        print(f"Backing up vault to {backup_dir}...")
        shutil.copytree(NOVELS_ROOT.parent, backup_dir / "vault", dirs_exist_ok=True)

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
                
            kind = "character" if kind_dir_name == "characters" else ("hook" if kind_dir_name == "hooks" else "world")
            
            for md_file in kind_path.glob("*.md"):
                rel_path = md_file.relative_to(NOVELS_ROOT.parent)
                ent_id = f"ent_{get_sha1_prefix(str(rel_path))}"
                name = md_file.stem
                now = datetime.now().isoformat()
                
                print(f"  [{kind}] {name} -> {ent_id}")
                
                if not dry_run:
                    cursor.execute(
                        """
                        INSERT INTO entities (id, project, kind, name, md_path, created_at, updated_at, properties)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            name=excluded.name,
                            md_path=excluded.md_path,
                            updated_at=excluded.updated_at
                        """,
                        (ent_id, project, kind, name, str(md_file), now, now, "{}")
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
    
    # Auto-detect vault path from env or current state
    # In this workspace, VAULT_ROOT is typically f:/hajimi/ObsidianVault
    migrate(dry_run=not args.run)
