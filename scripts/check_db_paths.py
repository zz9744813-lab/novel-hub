import sqlite3
from pathlib import Path

DB_PATH = Path("f:/hajimi/novel-hub/novelhub.db")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
try:
    rows = conn.execute("SELECT path FROM file_index").fetchall()
    print(f"Total rows: {len(rows)}")
    for r in rows[:5]:
        print(r["path"])
except Exception as e:
    print(e)
conn.close()
