from tests.test_helpers import configure_temp_runtime
import app.main as main

def test_log_operation_writes_project_and_value(tmp_path):
    configure_temp_runtime(tmp_path)
    main.log_operation("save", "chapter.md", "words_added=123", value=123, project="demo")
    with main.get_conn() as conn:
        row = conn.execute("SELECT action, target, project, detail, value FROM operation_logs").fetchone()
    assert row["action"] == "save"
    assert row["target"] == "chapter.md"
    assert row["project"] == "demo"
    assert row["detail"] == "words_added=123"
    assert row["value"] == 123

def test_compute_trend_uses_save_detail_words_added(tmp_path):
    configure_temp_runtime(tmp_path)
    main.log_operation("save", "chapter.md", "words_added=50", value=50, project="demo")
    trend = main.compute_trend()
    assert len(trend) == 7
    # trend[-1] is today
    assert trend[-1]["words"] == 50

def test_get_project_stats_aggregates_values(tmp_path):
    configure_temp_runtime(tmp_path)
    main.log_operation("save", "chapter.md", "words_added=100", value=100, project="demo")
    main.log_operation("save", "chapter2.md", "words_added=200", value=200, project="demo")
    stats = main.get_project_stats("demo", days=90)
    assert len(stats) >= 1
    assert stats[0]["words"] == 300
