"""Test that all critical modules import without errors."""
import importlib


def test_import_database():
    mod = importlib.import_module("app.database")
    assert hasattr(mod, "async_session_factory")
    assert hasattr(mod, "engine")


def test_import_config():
    mod = importlib.import_module("app.config")
    assert hasattr(mod, "settings")


def test_import_models():
    mod = importlib.import_module("app.models")
    assert hasattr(mod, "Chapter")
    assert hasattr(mod, "StoryEvent")
    assert hasattr(mod, "OutlineNode")
    assert hasattr(mod, "AgentRun")
    assert hasattr(mod, "AgentRunOutput")


def test_import_model_gateway():
    mod = importlib.import_module("app.gateway.model_gateway")
    assert hasattr(mod, "stream_with_retry")
    assert hasattr(mod, "stream_completion_and_collect")
    assert hasattr(mod, "StreamResult")
    assert hasattr(mod, "RETRYABLE_ERRORS")


def test_import_publish_pipeline():
    mod = importlib.import_module("app.gateway.publish_pipeline")
    assert hasattr(mod, "full_pipeline")
    assert hasattr(mod, "PublishState")


def test_import_normalizer():
    mod = importlib.import_module("app.gateway.normalizer")
    assert hasattr(mod, "normalize_json")


def test_import_leak_guard():
    mod = importlib.import_module("app.gateway.leak_guard")
    assert hasattr(mod, "check_leak")


def test_import_provider_adapter():
    mod = importlib.import_module("app.gateway.provider_adapter")
    assert hasattr(mod, "InlineReasoningParser")
    assert hasattr(mod, "CanonicalEventType")
    assert hasattr(mod, "THINK_OPEN")
    assert hasattr(mod, "THINK_CLOSE")
    assert hasattr(mod, "classify_delta")


def test_import_caller():
    mod = importlib.import_module("app.agents.caller")
    assert hasattr(mod, "call_agent")


def test_import_retrieval():
    mod = importlib.import_module("app.engine.retrieval")
    assert hasattr(mod, "candidate_merge_and_score")
    assert hasattr(mod, "event_ledger_search")
    assert hasattr(mod, "full_text_search")
    assert hasattr(mod, "query_planner_agent")
    assert hasattr(mod, "evidence_ranker_agent")
    assert hasattr(mod, "deterministic_query_template")
    assert hasattr(mod, "SCORE_WEIGHTS")


def test_import_pipeline():
    mod = importlib.import_module("app.engine.pipeline")
    assert hasattr(mod, "execute_pipeline")
    assert hasattr(mod, "_set_chapter_status")
    assert hasattr(mod, "_get_outline_node")


def test_import_state_machine():
    mod = importlib.import_module("app.state_machine")
    assert hasattr(mod, "ChapterState")
    assert hasattr(mod, "can_transition")


def test_import_context_assembler():
    mod = importlib.import_module("app.engine.context_assembler")
    assert hasattr(mod, "assemble_context")


def test_import_memory():
    mod = importlib.import_module("app.engine.memory")
    assert hasattr(mod, "commit_l4_with_events")


def test_import_prompts():
    mod = importlib.import_module("app.prompts")
    assert hasattr(mod, "PROMPTS")
    assert hasattr(mod, "AGENT_MODELS")
    assert hasattr(mod, "AGENT_TEMPERATURES")
    assert hasattr(mod, "AGENT_IS_JSON")

    # Verify all 9 agents have configs
    expected_agents = {
        "outline_parser", "chapter_planner", "draft_writer",
        "review_agent", "local_rewrite_editor", "state_extractor",
        "drift_audit", "query_planner", "evidence_ranker",
    }
    assert expected_agents.issubset(set(mod.AGENT_MODELS.keys()))
    assert expected_agents.issubset(set(mod.AGENT_TEMPERATURES.keys()))
    assert expected_agents.issubset(set(mod.AGENT_IS_JSON.keys()))
