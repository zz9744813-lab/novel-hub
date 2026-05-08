from __future__ import annotations

import sqlite3
from app.config import DB_PATH
from app.db import get_conn

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_index (
    path TEXT PRIMARY KEY,
    project TEXT,
    word_count INTEGER,
    updated_at TEXT,
    title TEXT,
    chapter TEXT,
    chapter_int INTEGER,
    status TEXT,
    volume TEXT,
    mtime REAL,
    synopsis TEXT
);

CREATE INDEX IF NOT EXISTS idx_fi_project ON file_index(project, chapter_int);
CREATE INDEX IF NOT EXISTS idx_file_index_project_chapter ON file_index(project, chapter_int);
CREATE INDEX IF NOT EXISTS idx_file_index_project_volume ON file_index(project, volume, chapter_int);

CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    target TEXT,
    created_at TEXT NOT NULL,
    detail TEXT,
    project TEXT,
    value INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS project_meta (
    project TEXT PRIMARY KEY,
    target_words INTEGER DEFAULT 100000,
    author TEXT DEFAULT '',
    synopsis TEXT DEFAULT '',
    daily_goal INTEGER DEFAULT 2000
);

CREATE VIRTUAL TABLE IF NOT EXISTS chapter_fts USING fts5(
    path UNINDEXED,
    project UNINDEXED,
    kind UNINDEXED,
    title,
    body,
    tokenize='unicode61 remove_diacritics 2'
);

-- C-Route (v6) Schema
CREATE TABLE IF NOT EXISTS consistency_reports (
    chapter_path TEXT PRIMARY KEY,
    created_at TEXT,
    issues TEXT
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    aliases TEXT,
    md_path TEXT,
    properties TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_entities_project_kind ON entities(project, kind);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(project, name);

CREATE TABLE IF NOT EXISTS entity_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    project TEXT NOT NULL,
    chapter_int INTEGER,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_entity ON entity_history(entity_id, chapter_int);

CREATE TABLE IF NOT EXISTS entity_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    notes TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_rel_source ON entity_relations(source_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON entity_relations(target_id);

CREATE TABLE IF NOT EXISTS scenes (
    id TEXT PRIMARY KEY,
    chapter_path TEXT NOT NULL,
    project TEXT NOT NULL,
    seq INTEGER NOT NULL,
    title TEXT,
    pov TEXT,
    location_id TEXT,
    word_count INTEGER,
    char_offset_start INTEGER,
    char_offset_end INTEGER,
    summary TEXT,
    status TEXT
);
CREATE INDEX IF NOT EXISTS idx_scenes_chapter ON scenes(chapter_path, seq);

CREATE TABLE IF NOT EXISTS entity_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_path TEXT NOT NULL,
    scene_id TEXT,
    entity_id TEXT NOT NULL,
    ref_kind TEXT,
    char_offset INTEGER
);
CREATE INDEX IF NOT EXISTS idx_refs_entity ON entity_refs(entity_id);
CREATE INDEX IF NOT EXISTS idx_refs_chapter ON entity_refs(chapter_path);

CREATE TABLE IF NOT EXISTS volumes (
    project TEXT NOT NULL,
    slug TEXT NOT NULL,
    title TEXT,
    seq INTEGER,
    synopsis TEXT,
    target_words INTEGER,
    PRIMARY KEY (project, slug)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    label TEXT,
    content_hash TEXT,
    content BLOB,
    protected INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_snapshots_chapter ON snapshots(chapter_path, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS entity_fts USING fts5(
    id UNINDEXED,
    project UNINDEXED,
    name,
    aliases,
    properties,
    tokenize='unicode61 remove_diacritics 2'
);
"""

def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=268435456")

def _ensure_legacy_columns(conn: sqlite3.Connection) -> None:
    """Handle migration of legacy columns.
    Note: Current implementation may skip subsequent ALTERs if the first one fails.
    Preserving this behavior for backward compatibility.
    """
    try:
        conn.execute("ALTER TABLE file_index ADD COLUMN title TEXT")
        conn.execute("ALTER TABLE file_index ADD COLUMN chapter TEXT")
        conn.execute("ALTER TABLE file_index ADD COLUMN chapter_int INTEGER")
        conn.execute("ALTER TABLE file_index ADD COLUMN status TEXT")
        conn.execute("ALTER TABLE file_index ADD COLUMN volume TEXT")
        conn.execute("ALTER TABLE file_index ADD COLUMN mtime REAL")
        conn.execute("ALTER TABLE file_index ADD COLUMN synopsis TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fi_project ON file_index(project, chapter_int)")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE operation_logs ADD COLUMN project TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE operation_logs ADD COLUMN value INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

def _ensure_operation_log_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_time_action ON operation_logs(created_at, action)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_target ON operation_logs(target)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_project ON operation_logs(project)")

def _migrate_chapter_fts_kind(conn: sqlite3.Connection) -> None:
    """Migrate chapter_fts to add 'kind' column if missing (FTS5 doesn't support ALTER)."""
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(chapter_fts)").fetchall()]
        if cols and "kind" not in cols:
            conn.execute("DROP TABLE chapter_fts")
            conn.execute("""
                CREATE VIRTUAL TABLE chapter_fts USING fts5(
                    path UNINDEXED, project UNINDEXED, kind UNINDEXED,
                    title, body, tokenize='unicode61 remove_diacritics 2'
                )
            """)
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("fts_needs_rebuild", "1"),
            )
    except sqlite3.OperationalError:
        pass

def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        _apply_pragmas(conn)
        conn.executescript(SCHEMA_SQL)
        _ensure_legacy_columns(conn)
        _ensure_operation_log_indexes(conn)
        _migrate_chapter_fts_kind(conn)
