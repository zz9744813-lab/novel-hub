from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import uuid

import pytest
from pydantic import ValidationError

from app.contracts.agents import MemorySummaryContract, schema_for_role
from app.engine.chapter_target import (
    chapter_length_issues,
    distribute_scene_targets,
    parse_chapter_target_chars,
)
from app.engine.memory_compiler import volume_stage_window
from app.agents.drift_audit import classify_drift_status, stratified_chapter_numbers
from app.models import (
    MemoryL4StateSnapshot,
    ModelCatalog,
    ModelHealthSnapshot,
    OutlineNode,
    StyleToneAnchor,
    StyleVoiceCard,
)
from app.production_pack import (
    ProductionPackValidationError,
    load_and_validate_pack,
    validate_pack,
)
from app.production_pack.service import install_production_pack, stable_id
from app.production_pack.release_gate import (
    evaluate_release_snapshot,
    expected_drift_ranges,
    expected_l2_ranges,
    release_sample_chapters,
    scan_text_reference_overlap,
)


PACK_ROOT = (
    Path(__file__).resolve().parents[1]
    / "production_packs"
    / "zhutian_hongyanlu"
)
PACK_MANIFEST = PACK_ROOT / "pack.json"
PRODUCTION_PACK_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "production_pack.py"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RELEASE_SCRIPT = REPOSITORY_ROOT / "deploy" / "ops" / "novelforge-release"
FORCED_COMMAND_SCRIPT = REPOSITORY_ROOT / "deploy" / "ops" / "novelforge-ops"
CONSOLE_BOOTSTRAP_SCRIPT = (
    REPOSITORY_ROOT / "deploy" / "ops" / "bootstrap-console.sh"
)


def _pack():
    return load_and_validate_pack(PACK_MANIFEST)[0]


def _memory_summary_payload(summary_type: str) -> dict:
    return {
        "summary_type": summary_type,
        "stage_goal": "守住盟约并推进当前阶段目标",
        "conflict_changes": ["外部压力升级"],
        "character_arcs": ["主角开始承担联盟责任"],
        "state_changes": ["关键据点转为己方控制"],
        "open_questions": ["幕后势力身份仍未确认"],
        "next_constraints": ["下一阶段不得破坏既有盟约"],
    }


def test_memory_compiler_contract_accepts_only_declared_summary_levels():
    assert MemorySummaryContract.model_validate(
        _memory_summary_payload("l2_stage")
    ).summary_type == "l2_stage"
    assert MemorySummaryContract.model_validate(
        _memory_summary_payload("l3_volume")
    ).summary_type == "l3_volume"

    with pytest.raises(ValidationError):
        MemorySummaryContract.model_validate(_memory_summary_payload("chapter"))

    schema = schema_for_role("memory_compiler")
    assert schema is not None
    assert schema["properties"]["summary_type"]["enum"] == [
        "l2_stage",
        "l3_volume",
    ]


def test_builtin_pack_is_complete_and_deterministic():
    pack, report = load_and_validate_pack(PACK_MANIFEST)

    assert report.passed is True
    assert report.errors == []
    assert report.counts == {
        "sources": 2,
        "characters": 8,
        "relationships": 12,
        "world_rules": 8,
        "locations": 10,
        "plot_threads": 10,
        "events": 24,
        "chapters": 96,
        "volumes": 6,
    }
    assert [chapter.chapter_no for chapter in pack.chapters] == list(range(1, 97))
    assert [volume.chapter_to - volume.chapter_from + 1 for volume in pack.volumes] == [16] * 6
    assert pack.canonical_sha256() == report.pack_sha256
    assert len(report.pack_sha256) == 64


def test_checked_in_chapters_match_executable_blueprint():
    module_path = PACK_ROOT / "build_chapters.py"
    spec = importlib.util.spec_from_file_location("zhutian_hongyanlu_blueprint", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    generated = module.build()
    checked_in = json.loads((PACK_ROOT / "chapters.json").read_text(encoding="utf-8"))
    assert generated == checked_in


def test_cli_reference_file_must_match_a_declared_raw_sha256(tmp_path: Path):
    spec = importlib.util.spec_from_file_location(
        "production_pack_cli_for_test", PRODUCTION_PACK_SCRIPT
    )
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    content = "仅用于验证原始文件版本，不进入写作上下文。"
    reference = tmp_path / "reference.txt"
    reference.write_text(content, encoding="utf-8")
    pack = _pack().model_copy(deep=True)
    pack.sources[0].sha256 = hashlib.sha256(reference.read_bytes()).hexdigest()

    assert cli._verified_reference_texts(pack, [str(reference)]) == [content]

    reference.write_text(content + "已变更", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 is not declared"):
        cli._verified_reference_texts(pack, [str(reference)])


def test_restricted_release_runs_model_evidence_before_switching():
    release = RELEASE_SCRIPT.read_text(encoding="utf-8")
    forced = FORCED_COMMAND_SCRIPT.read_text(encoding="utf-8")

    qualification = "production_pack.py qualify"
    assert qualification in release
    assert release.index(qualification) < release.index('switch_to "$release"')
    assert "validate|qualify|install|start" in release
    assert "validate|qualify|install|start" in forced


def test_console_bootstrap_is_pinned_and_never_interprets_the_key_as_shell():
    bootstrap = CONSOLE_BOOTSTRAP_SCRIPT.read_text(encoding="utf-8")

    assert "OPS_COMMIT=7c5aaee1a1d5b4683248db8ef794b55c8d68dfe1" in bootstrap
    assert "KEY_BODY=$1" in bootstrap
    assert "printf 'ssh-ed25519 %s" in bootstrap
    assert "/root/novelforge/deploy/.env /root/novelforge/.env" in bootstrap
    assert "NOVELFORGE_BOOTSTRAP_OK" in bootstrap
    assert "eval " not in bootstrap
    assert "bash -c" not in bootstrap


@pytest.mark.asyncio
async def test_production_qualification_reuses_current_evidence_without_calls():
    from unittest.mock import AsyncMock, patch

    from app.production_pack.model_evidence import ensure_configured_model_evidence

    catalog = ModelCatalog(
        id=uuid.uuid4(),
        provider="primary",
        model_id="configured-writer",
        enabled=True,
        auto_route_enabled=True,
        availability_status="available",
        model_kind="text_generation",
        text_generation_eligible=True,
        metadata_json={},
    )
    snapshot = ModelHealthSnapshot(
        id=uuid.uuid4(),
        model_catalog_id=catalog.id,
        health_status="healthy",
    )

    class Rows:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class Session:
        def __init__(self):
            self.added = []

        async def execute(self, statement):
            table = statement._raw_columns[0].name
            return Rows(catalog if table == "model_catalog" else snapshot)

        def add(self, value):
            self.added.append(value)

        async def commit(self):
            return None

        async def refresh(self, value):
            return None

    session = Session()

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    cached = {
        "status": "succeeded",
        "execution_complete": True,
        "reused": True,
        "gateway_calls": 0,
    }
    evidence_state = {
        "ability": {"state": "valid"},
        "context": {"state": "valid"},
        "role_evidence": {
            "draft_writer": {"state": "valid", "passed": True},
        },
        "context_profile": {"effective": 128_000},
    }

    with patch(
        "app.database.async_session_factory", return_value=SessionContext()
    ), patch(
        "app.main.ensure_required_bindings", AsyncMock()
    ), patch(
        "app.production_pack.model_evidence.bootstrap_catalog_and_probes",
        AsyncMock(return_value={"probed": 0, "skipped_fresh": 1, "errors": []}),
    ), patch(
        "app.production_pack.model_evidence._effective_targets",
        AsyncMock(
            return_value=({("primary", "configured-writer"): {"draft_writer"}}, [])
        ),
    ), patch(
        "app.model_eval.engine.run_qualification",
        AsyncMock(return_value=cached),
    ) as ability, patch(
        "app.model_eval.engine.run_context_ladder",
        AsyncMock(return_value=cached),
    ) as context, patch(
        "app.model_eval.engine.get_catalog_evidence_state",
        AsyncMock(return_value=evidence_state),
    ):
        report = await ensure_configured_model_evidence(_pack())

    assert report["passed"] is True
    assert report["counts"] == {
        "configured_models": 1,
        "evaluated_models": 1,
        "gateway_calls": 0,
        "reused_models": 1,
    }
    ability.assert_awaited_once()
    context.assert_awaited_once()


def test_every_key_event_is_present_at_its_declared_chapter():
    pack = _pack()
    chapters = {chapter.chapter_no: chapter for chapter in pack.chapters}

    for event in pack.event_graph.nodes:
        assert event.event_id in chapters[event.chapter_no].event_ids


def test_reference_overlap_gate_hashes_evidence_without_echoing_text():
    pack = _pack().model_copy(deep=True)
    synthetic_reference = "这是一段只为残留扫描构造的连续独特文本不会进入任何创作上下文"
    pack.book.logline = synthetic_reference

    report = validate_pack(pack, reference_texts=[synthetic_reference])
    overlap = [item for item in report.errors if item.code == "REFERENCE_NGRAM_OVERLAP"]

    assert overlap
    assert all("hash=" in item.message for item in overlap)
    assert all(synthetic_reference not in item.message for item in overlap)


def test_reference_residue_and_orphan_key_event_fail_closed():
    residue_pack = _pack().model_copy(deep=True)
    residue_pack.book.logline += " 琼明神女录"
    residue_report = validate_pack(residue_pack)
    assert "REFERENCE_RESIDUE" in {item.code for item in residue_report.errors}

    orphan_pack = _pack().model_copy(deep=True)
    orphan_pack.event_graph.edges = [
        edge for edge in orphan_pack.event_graph.edges if edge.target != "EV-24"
    ]
    orphan_report = validate_pack(orphan_pack)
    assert "EVENT_CAUSAL_INBOUND" in {item.code for item in orphan_report.errors}


def test_source_independence_and_event_placement_cannot_be_gamed():
    duplicate_source_pack = _pack().model_copy(deep=True)
    duplicate = duplicate_source_pack.sources[0].model_copy(
        update={"source_id": "SRC-DUPLICATE"}
    )
    duplicate_source_pack.sources.append(duplicate)
    duplicate_source_pack.style.source_ids.append(duplicate.source_id)
    duplicate_source_pack.style.confidence = "high"
    duplicate_report = validate_pack(duplicate_source_pack)
    duplicate_codes = {item.code for item in duplicate_report.errors}

    assert {"DUPLICATE_SOURCE_HASH", "STYLE_CONFIDENCE"} <= duplicate_codes

    misplaced_event_pack = _pack().model_copy(deep=True)
    event_id = misplaced_event_pack.chapters[0].event_ids.pop()
    misplaced_event_pack.chapters[1].event_ids.append(event_id)
    misplaced_report = validate_pack(misplaced_event_pack)

    assert "EVENT_CHAPTER_PLACEMENT" in {
        item.code for item in misplaced_report.errors
    }


def test_bundle_member_cannot_escape_pack_directory(tmp_path: Path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "pack.json").write_text(
        json.dumps(
            {
                "bundle_version": "1.0",
                "core": "../outside.json",
                "chapters": "../outside.json",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid production-pack bundle member"):
        load_and_validate_pack(bundle / "pack.json")


def test_invalid_pack_raises_with_machine_readable_report():
    pack = _pack().model_copy(deep=True)
    pack.sources[0].raw_reference_in_drafting_context = True
    report = validate_pack(pack)
    error = ProductionPackValidationError(report)

    assert report.passed is False
    assert error.report is report
    assert "RAW_REFERENCE_CONTEXT" in str(error)


def test_chapter_length_contract_and_scene_distribution_are_exact():
    target = parse_chapter_target_chars("[4200, 5800]")

    assert (target.minimum_chars, target.target_chars, target.maximum_chars) == (
        4200,
        5000,
        5800,
    )
    assert distribute_scene_targets(target.target_chars, [1, 2, 1]) == [1375, 2250, 1375]
    assert sum(distribute_scene_targets(5001, [900, None, 1200, 600])) == 5001
    assert chapter_length_issues("字" * 4199, target)[0]["issue_id"] == (
        "chapter_length_below_contract"
    )
    assert chapter_length_issues("字" * 5000, target) == []
    assert chapter_length_issues("字" * 5801, target)[0]["issue_id"] == (
        "chapter_length_above_contract"
    )
    with pytest.raises(ValueError, match="ordered"):
        parse_chapter_target_chars("[6000, 5000]")


def test_memory_windows_never_cross_a_volume_boundary():
    assert volume_stage_window(1, 1, 16) == (1, 10)
    assert volume_stage_window(10, 1, 16) == (1, 10)
    assert volume_stage_window(11, 1, 16) == (11, 16)
    assert volume_stage_window(16, 1, 16) == (11, 16)
    assert volume_stage_window(17, 17, 32) == (17, 26)
    assert volume_stage_window(32, 17, 32) == (27, 32)
    with pytest.raises(ValueError, match="inside"):
        volume_stage_window(33, 17, 32)


def test_drift_samples_cover_start_middle_and_end():
    sample = stratified_chapter_numbers(1, 30, count=6)
    assert sample == [1, 7, 13, 18, 24, 30]
    assert stratified_chapter_numbers(91, 96, count=6) == [91, 92, 93, 94, 95, 96]
    with pytest.raises(ValueError, match="sampling"):
        stratified_chapter_numbers(10, 1)


def test_drift_thresholds_are_fail_closed():
    green = {
        "state_card_accuracy": 0.99,
        "retrieval_recall_at_8": 0.94,
        "retrieval_precision_at_8": 0.72,
        "required_fact_injection_rate": 1.0,
        "outline_adherence": 0.96,
        "character_voice_consistency": 0.91,
        "narrative_tone_anchor_score": 0.91,
    }
    assert classify_drift_status(green, requested_status="green") == "green"
    assert (
        classify_drift_status(
            {**green, "character_voice_consistency": 0.79},
            requested_status="green",
        )
        == "yellow"
    )
    assert (
        classify_drift_status(
            {**green, "required_fact_injection_rate": 0.99},
            requested_status="green",
        )
        == "red"
    )
    assert (
        classify_drift_status(green, requested_status="green", redline_findings=[{}])
        == "red"
    )
    with pytest.raises(ValueError, match="missing drift metrics"):
        classify_drift_status({}, requested_status="green")


def test_release_audit_identity_includes_pack_sha():
    from sqlalchemy import UniqueConstraint

    from app.models import ManuscriptReleaseAudit

    constraint = next(
        item
        for item in ManuscriptReleaseAudit.__table__.constraints
        if isinstance(item, UniqueConstraint)
        and item.name == "uq_manuscript_release_evidence"
    )
    assert set(constraint.columns.keys()) == {
        "book_id",
        "manuscript_hash",
        "gate_version",
        "production_pack_sha256",
    }


def _passing_release_snapshot(pack):
    chapters = []
    total = 0
    for number in range(1, 97):
        seed = f"第{number}份匿名正文以人物选择推动不可逆变化。"
        content = (seed + "山川灯火人心进退皆有因果。" * 500)[:5000]
        total += len(content)
        chapters.append(
            {
                "chapter_no": number,
                "title": f"原创章名{number}",
                "status": "finalized",
                "version_kind": "final",
                "content": content,
                "content_hash": __import__("hashlib").sha256(content.encode()).hexdigest(),
                "scene_count": 3,
                "contract_count": 3,
                "event_count": 3,
                "edge_count": 2,
                "ledger_count": 1,
            }
        )
    return {
        "production_pack_id": pack.pack_id,
        "production_pack_revision": pack.revision,
        "production_pack_sha256": pack.canonical_sha256(),
        "book": {"finalized_chapters": 96, "finalized_words": total},
        "chapters": chapters,
        "l2_summaries": [
            {"start": start, "end": end, "status": "generated", "has_error": False}
            for start, end in expected_l2_ranges(pack)
        ],
        "l3_summaries": [
            {"volume_no": number, "status": "generated", "has_error": False}
            for number in range(1, 7)
        ],
        "drift_reports": [
            {"start": 1, "end": 30, "status": "green", "redline_findings": []},
            {"start": 31, "end": 60, "status": "green", "redline_findings": []},
            {"start": 61, "end": 90, "status": "yellow", "redline_findings": []},
            {"start": 91, "end": 96, "status": "green", "redline_findings": []},
        ],
        "l4_character_ids": [
            str(stable_id(pack.pack_id, "character", item.character_id))
            for item in pack.characters
        ],
        "initial_l4_count": 8,
        "writing_sessions": [
            {
                "status": "completed",
                "stop_reason": "outline_exhausted",
                "chapters_completed": 96,
            }
        ],
    }


def test_release_gate_requires_every_runtime_artifact_before_blind_review():
    pack = _pack()
    snapshot = _passing_release_snapshot(pack)

    report = evaluate_release_snapshot(
        pack,
        snapshot,
        reference_texts=["这份合成参考与匿名正文不存在连续十六字相同内容。"],
    )

    assert report.status == "deterministic_pass"
    assert report.counts["chapters"] == 96
    assert report.counts["total_chars"] == 480000
    assert not [item for item in report.findings if item.severity == "blocker"]
    assert release_sample_chapters(pack) == [
        1, 8, 16, 17, 24, 32, 33, 40, 48,
        49, 56, 64, 65, 72, 80, 81, 88, 96,
    ]
    assert expected_drift_ranges(pack) == [
        (1, 30),
        (31, 60),
        (61, 90),
        (91, 96),
    ]

    snapshot["chapters"][42]["ledger_count"] = 0
    snapshot["l3_summaries"] = [
        item for item in snapshot["l3_summaries"] if item["volume_no"] != 4
    ]
    snapshot["writing_sessions"][0]["status"] = "running"
    failed = evaluate_release_snapshot(pack, snapshot, reference_texts=["无重合参考文本"])
    codes = {item.code for item in failed.findings}
    assert failed.status == "failed"
    assert {"L1_LEDGER_MISSING", "L3_VOLUME_SUMMARY_MISSING", "WRITING_SESSION_ACTIVE"} <= codes


def test_release_gate_allows_restarted_sessions_but_rejects_degraded_memory():
    pack = _pack()
    snapshot = _passing_release_snapshot(pack)
    snapshot["writing_sessions"] = [
        {"status": "failed", "stop_reason": "resource_block", "chapters_completed": 40},
        {"status": "completed", "stop_reason": "outline_exhausted", "chapters_completed": 56},
    ]
    snapshot["l2_summaries"][3]["status"] = "degraded"
    snapshot["l2_summaries"][3]["has_error"] = True
    snapshot["drift_reports"][1]["yellow_findings"] = [
        {"type": "audit_service_failure", "detail": "timeout"}
    ]

    failed = evaluate_release_snapshot(pack, snapshot)
    codes = {item.code for item in failed.findings}

    assert "WRITING_SESSION_NOT_EXHAUSTED" not in codes
    assert {"L2_STAGE_SUMMARY_DEGRADED", "DRIFT_AUDIT_UNAVAILABLE"} <= codes


def test_final_reference_scan_returns_hashes_not_source_prose():
    overlap = "这是一段恰好超过十六个字符的唯一参考残留"
    report = scan_text_reference_overlap(
        f"原创开头。{overlap}。原创结尾。",
        [f"参考前文。{overlap}。参考后文。"],
    )

    serialized = json.dumps(report, ensure_ascii=False)
    assert report["passed"] is False
    assert report["overlap_count"] > 0
    assert overlap not in serialized
    assert len(report["manuscript_text_sha256"]) == 64
    assert all(len(value) == 64 for value in report["reference_text_sha256"])


class _EmptyResult:
    def scalar_one_or_none(self):
        return None


class _RecordingSession:
    def __init__(self):
        self.added = []
        self.flush_count = 0

    async def execute(self, _statement):
        return _EmptyResult()

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1


def test_installer_materializes_all_chapter_contracts_without_reference_prose():
    pack = _pack()
    db = _RecordingSession()

    result = asyncio.run(install_production_pack(db, pack))

    nodes = [item for item in db.added if isinstance(item, OutlineNode)]
    initial_states = [item for item in db.added if isinstance(item, MemoryL4StateSnapshot)]
    voices = [item for item in db.added if isinstance(item, StyleVoiceCard)]
    tones = [item for item in db.added if isinstance(item, StyleToneAnchor)]
    creative_blob = json.dumps(
        [
            {
                "title": node.title,
                "goal": node.goal,
                "required_beats": node.required_beats,
                "forbidden_outcomes": node.forbidden_outcomes,
            }
            for node in nodes
        ],
        ensure_ascii=False,
    )

    assert result["status"] == "installed"
    assert result["book_id"] == str(stable_id(pack.pack_id, "book", "root"))
    assert db.flush_count == 1
    assert len(nodes) == 96
    assert len(initial_states) == 8
    assert all(item.as_of_chapter == 0 and item.is_locked for item in initial_states)
    assert all(item.state["goals"]["external_goal"]["status"] == "active" for item in initial_states)
    assert all("initial_false_belief" in item.state["beliefs"] for item in initial_states)
    assert len(voices) == 8 and all(item.approved_examples == [] for item in voices)
    assert len(tones) == 1 and tones[0].approved_samples == []
    assert not any(residue in creative_blob for residue in pack.reference_residue_denylist)
