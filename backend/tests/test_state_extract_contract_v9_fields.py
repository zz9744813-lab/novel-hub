"""P1 regression: StateExtractContract must cover the v9 fields that
state_extractor's prompt explicitly requests — a strict json_schema missing
them forces the model to drop/mangle events (18/18 malformed in production)."""
import json

from app.contracts.agents import StateExtractContract
from app.contracts.narrative import ExtractedStoryEvent


def _sample_event():
    return {
        "event_key": "EVT-001-01",
        "entity_type": "character",
        "entity_id": "8006e0e8-2614-5c76-a884-4a7b4dcfd7f1",
        "field": "injury_status",
        "old_value": None,
        "new_value": "left_arm_sprained",
        "certainty": "explicit",
        "scene_no": 1,
        "evidence_paragraph_key": "p-003",
        "evidence_hash": "abc123",
        "evidence": "她落地时踉跄了一下，扶住左臂。",
        "realized_provisional_event_key": "P-001-02-01",
    }


def test_extract_event_contract_accepts_realized_provisional_key():
    model = StateExtractContract.model_validate({"events": [_sample_event()]})
    assert model.events[0].realized_provisional_event_key == "P-001-02-01"


def test_extract_contract_accepts_reaction_evidence_and_attributions():
    payload = {
        "events": [_sample_event()],
        "conflicts": [],
        "l1_chapter_ledger": None,
        "reaction_evidence": [
            {
                "reaction_key": "R-001",
                "character_id": "8006e0e8-2614-5c76-a884-4a7b4dcfd7f1",
                "scene_no": 1,
                "evidence_paragraph_key": "p-004",
                "reaction_summary": "她握紧了剑柄。",
                "weight": 0.8,
            }
        ],
        "attributions": [
            {
                "reaction_key": "R-001",
                "cause_event_keys": ["EVT-001-01"],
                "core_anchor_ids": [],
                "belief_keys": [],
                "goal_keys": [],
                "relationship_refs": [],
                "status": "resolved",
                "reason": "直接因果",
            }
        ],
    }
    model = StateExtractContract.model_validate(payload)
    assert len(model.reaction_evidence) == 1
    assert len(model.attributions) == 1
    assert model.reaction_evidence[0].reaction_key == "R-001"


def test_extracted_story_event_still_validates_contract_output():
    """The second-layer ExtractedStoryEvent validation must accept what the
    (now-correct) json_schema lets through."""
    payload = _sample_event()
    evt = ExtractedStoryEvent.model_validate(payload)
    assert evt.event_key == "EVT-001-01"
    assert evt.realized_provisional_event_key == "P-001-02-01"


def test_schema_roundtrip_json_serializable():
    model = StateExtractContract.model_validate({"events": [_sample_event()]})
    json.dumps(model.model_dump(mode="json"), ensure_ascii=False)