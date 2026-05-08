from tests.test_helpers import configure_temp_runtime
import app.main as main

def test_init_db_creates_core_tables(tmp_path):
    configure_temp_runtime(tmp_path)
    with main.get_conn() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            ).fetchall()
        }
    for table in [
        "settings", "file_index", "operation_logs", "project_meta",
        "entities", "entity_history", "entity_relations", "scenes",
        "entity_refs", "volumes", "snapshots"
    ]:
        assert table in tables

def test_init_db_pragmas_are_applied(tmp_path):
    configure_temp_runtime(tmp_path)
    with main.get_conn() as conn:
        # Check some pragmas that are usually applied on connection
        # busy_timeout is 5000 in our get_conn
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        # WAL mode is persistent
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

def test_init_db_file_index_has_synopsis_column(tmp_path):
    configure_temp_runtime(tmp_path)
    with main.get_conn() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(file_index)").fetchall()}
    assert "synopsis" in cols
