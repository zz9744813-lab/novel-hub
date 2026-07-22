import os
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import NOVELS_ROOT, get_conn, list_markdown_files

def migrate_volumes():
    moved_count = 0
    
    if not NOVELS_ROOT.exists():
        print("Vault root not found, nothing to migrate.")
        return

    for project_dir in NOVELS_ROOT.iterdir():
        if not project_dir.is_dir():
            continue
            
        chapters_dir = project_dir / "chapters"
        if not chapters_dir.exists():
            continue
            
        vol_01_dir = chapters_dir / "volume-01"
        
        # list_markdown_files now rglobs, so we only want files directly under chapters/
        # or files that are NOT under any volume-* folder (just in case)
        for filepath in chapters_dir.glob("*.md"):
            if not filepath.is_file():
                continue
                
            if not vol_01_dir.exists():
                vol_01_dir.mkdir(parents=True)
                
            new_filepath = vol_01_dir / filepath.name
            print(f"Moving {filepath} -> {new_filepath}")
            
            filepath.rename(new_filepath)
            moved_count += 1
            
            with get_conn() as conn:
                conn.execute(
                    "UPDATE file_index SET path = ? WHERE path = ?",
                    (str(new_filepath), str(filepath))
                )
                conn.execute(
                    "UPDATE chapter_fts SET path = ? WHERE path = ?",
                    (str(new_filepath), str(filepath))
                )
                    
    print(f"Volume migration complete! Moved {moved_count} files into volume-01.")

if __name__ == "__main__":
    migrate_volumes()
