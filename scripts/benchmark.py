"""
Generate fake data and benchmark critical queries.
Usage: python scripts/benchmark.py [--chapters 3000] [--refs 1000000]
"""
import argparse
import sqlite3
import time
import os
import random
import hashlib
from pathlib import Path

ROOT = Path(r"F:\novel-hub").resolve()
DB_PATH = Path(os.getenv("NOVELHUB_DB_PATH", str(ROOT / "novelhub.db")))


def gen_fake_data(conn, n_chapters: int, n_refs: int):
    project = "_bench"
    print(f"Generating {n_chapters} chapters and {n_refs} entity_refs for project {project}...")
    
    conn.execute("DELETE FROM file_index WHERE project=?", (project,))
    conn.execute("DELETE FROM entities WHERE project=?", (project,))
    conn.execute("DELETE FROM entity_refs WHERE chapter_path LIKE ?", (f"%/{project}/%",))
    conn.execute("DELETE FROM scenes WHERE project=?", (project,))
    conn.execute("DELETE FROM volumes WHERE project=?", (project,))
    
    # Volumes
    n_vols = max(1, n_chapters // 200)
    for i in range(n_vols):
        conn.execute(
            "INSERT INTO volumes(project, slug, title, seq) VALUES (?, ?, ?, ?)",
            (project, f"volume-{i+1:02d}", f"卷 {i+1}", i+1)
        )
    
    # Chapters
    for i in range(1, n_chapters + 1):
        vol = f"volume-{((i-1) // 200) + 1:02d}"
        path = f"/fake/{project}/chapters/{vol}/{i:05d}-ch.md"
        conn.execute(
            """INSERT INTO file_index(path, project, word_count, updated_at, title, chapter, chapter_int, status, volume, mtime, synopsis)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (path, project, random.randint(2000, 6000), "2025-01-01", f"第{i}章", str(i), i,
             random.choice(["draft", "polish", "done"]), vol, time.time(), "")
        )
    
    # Entities
    n_ents = max(50, n_chapters // 30)
    ent_ids = []
    for i in range(n_ents):
        eid = "ent_" + hashlib.sha1(f"{i}".encode()).hexdigest()[:8]
        kind = random.choice(["character", "location", "thread", "item"])
        conn.execute(
            """INSERT INTO entities(id, project, kind, name, properties, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (eid, project, kind, f"实体{i}", "{}", "2025-01-01", "2025-01-01")
        )
        ent_ids.append(eid)
    
    # entity_refs
    print(f"  Inserting {n_refs} entity_refs (this may take a while)...")
    chunk = []
    for i in range(n_refs):
        ch_int = random.randint(1, n_chapters)
        vol = f"volume-{((ch_int-1) // 200) + 1:02d}"
        path = f"/fake/{project}/chapters/{vol}/{ch_int:05d}-ch.md"
        chunk.append((path, None, random.choice(ent_ids), "wiki", random.randint(0, 5000)))
        if len(chunk) >= 5000:
            conn.executemany(
                "INSERT INTO entity_refs(chapter_path, scene_id, entity_id, ref_kind, char_offset) VALUES (?, ?, ?, ?, ?)",
                chunk
            )
            chunk = []
    if chunk:
        conn.executemany(
            "INSERT INTO entity_refs(chapter_path, scene_id, entity_id, ref_kind, char_offset) VALUES (?, ?, ?, ?, ?)",
            chunk
        )
    conn.commit()
    print("  Done.")
    return project, ent_ids


def bench(name, fn, target_ms):
    start = time.perf_counter()
    fn()
    elapsed = (time.perf_counter() - start) * 1000
    status = "✓" if elapsed < target_ms else "✗ SLOW"
    print(f"  [{status}] {name}: {elapsed:.1f}ms (target <{target_ms}ms)")
    return elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chapters", type=int, default=3000)
    parser.add_argument("--refs", type=int, default=1_000_000)
    parser.add_argument("--skip-gen", action="store_true")
    args = parser.parse_args()
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    
    if not args.skip_gen:
        project, ent_ids = gen_fake_data(conn, args.chapters, args.refs)
    else:
        project = "_bench"
        ent_ids = [r["id"] for r in conn.execute("SELECT id FROM entities WHERE project=?", (project,)).fetchall()]
    
    if not ent_ids:
        print("No entities found, skip bench")
        return
    sample_ent = random.choice(ent_ids)
    
    print(f"\nBenchmarks (project={project}):")
    
    bench("outline (vols + chapters + scenes)", lambda: (
        conn.execute("SELECT * FROM volumes WHERE project=? ORDER BY seq", (project,)).fetchall(),
        conn.execute("SELECT * FROM file_index WHERE project=? ORDER BY volume, chapter_int", (project,)).fetchall(),
        conn.execute("SELECT * FROM scenes WHERE project=? ORDER BY chapter_path, seq", (project,)).fetchall(),
    ), target_ms=200)
    
    bench("entity appearances (single entity reverse)", lambda:
        conn.execute("SELECT * FROM entity_refs WHERE entity_id=?", (sample_ent,)).fetchall(),
        target_ms=100)
    
    bench("FTS5 search ('某 主角')", lambda:
        conn.execute("SELECT path FROM chapter_fts WHERE chapter_fts MATCH ? AND project=? LIMIT 50",
                     ('"主角"', project)).fetchall(),
        target_ms=500)
    
    bench("file_index single chapter lookup", lambda:
        conn.execute("SELECT * FROM file_index WHERE project=? AND chapter_int=1500", (project,)).fetchone(),
        target_ms=300)
    
    bench("threads board (filter by kind)", lambda:
        conn.execute("SELECT * FROM entities WHERE project=? AND kind='thread'", (project,)).fetchall(),
        target_ms=100)
    
    print("\nDone. To clean up: python scripts/benchmark.py --skip-gen, then DELETE FROM all tables WHERE project='_bench'")


if __name__ == "__main__":
    main()
