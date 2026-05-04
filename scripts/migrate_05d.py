import os
import re
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import NOVELS_ROOT, get_conn, list_markdown_files

def migrate_chapters():
    pattern = re.compile(r"^(\d{1,4})-(.*\.md)$")
    renamed_count = 0
    
    if not NOVELS_ROOT.exists():
        print("Vault root not found, nothing to migrate.")
        return

    for project_dir in NOVELS_ROOT.iterdir():
        if not project_dir.is_dir():
            continue
            
        chapters_dir = project_dir / "chapters"
        if not chapters_dir.exists():
            continue
            
        for filepath in list_markdown_files(chapters_dir):
            match = pattern.match(filepath.name)
            if match:
                idx_str = match.group(1)
                rest = match.group(2)
                
                # If it's already 5 digits or more, skip
                if len(idx_str) >= 5:
                    continue
                    
                new_idx = f"{int(idx_str):05d}"
                new_name = f"{new_idx}-{rest}"
                new_filepath = filepath.with_name(new_name)
                
                print(f"Renaming {filepath.name} -> {new_name}")
                
                # Rename the file on disk
                filepath.rename(new_filepath)
                renamed_count += 1
                
                # Update file_index in database
                with get_conn() as conn:
                    # Update path in DB to new path
                    conn.execute(
                        "UPDATE file_index SET path = ? WHERE path = ?",
                        (str(new_filepath), str(filepath))
                    )
                    
    print(f"Migration complete! Renamed {renamed_count} files.")

if __name__ == "__main__":
    migrate_chapters()
