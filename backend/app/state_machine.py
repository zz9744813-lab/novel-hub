"""Chapter state machine - §7.1 v7.3 spec."""
from enum import Enum


class ChapterState(str, Enum):
    QUEUED = "queued"
    DEPENDENCY_CHECK = "dependency_check"
    CONTEXT_BUILDING = "context_building"
    PLANNING = "planning"
    DRAFTING = "drafting"
    REVIEWING = "reviewing"
    PATCHING = "patching"
    CONSISTENCY_CHECK = "consistency_check"
    STATE_EXTRACTING = "state_extracting"
    FINALIZING = "finalizing"
    FINALIZED = "finalized"
    NEEDS_HUMAN = "needs_human"
    BLOCKED_BY_DEPENDENCY = "blocked_by_dependency"
    RESOURCE_BLOCKED = "resource_blocked"
    FAILED = "failed"


# Legal transitions
TRANSITIONS = {
    ChapterState.QUEUED: {ChapterState.DEPENDENCY_CHECK, ChapterState.BLOCKED_BY_DEPENDENCY, ChapterState.FAILED},
    ChapterState.DEPENDENCY_CHECK: {ChapterState.CONTEXT_BUILDING, ChapterState.BLOCKED_BY_DEPENDENCY, ChapterState.FAILED},
    ChapterState.BLOCKED_BY_DEPENDENCY: {ChapterState.DEPENDENCY_CHECK, ChapterState.FAILED},
    ChapterState.CONTEXT_BUILDING: {ChapterState.PLANNING, ChapterState.FAILED},
    ChapterState.PLANNING: {ChapterState.DRAFTING, ChapterState.FAILED},
    ChapterState.DRAFTING: {ChapterState.REVIEWING, ChapterState.FAILED},
    ChapterState.REVIEWING: {ChapterState.PATCHING, ChapterState.CONSISTENCY_CHECK, ChapterState.FAILED},
    ChapterState.PATCHING: {ChapterState.CONSISTENCY_CHECK, ChapterState.REVIEWING, ChapterState.NEEDS_HUMAN, ChapterState.FAILED},
    ChapterState.CONSISTENCY_CHECK: {ChapterState.STATE_EXTRACTING, ChapterState.PATCHING, ChapterState.NEEDS_HUMAN, ChapterState.FAILED},
    ChapterState.STATE_EXTRACTING: {ChapterState.FINALIZING, ChapterState.FAILED},
    ChapterState.FINALIZING: {ChapterState.FINALIZED, ChapterState.FAILED},
    ChapterState.FINALIZED: set(),  # terminal
    ChapterState.NEEDS_HUMAN: {ChapterState.QUEUED, ChapterState.PATCHING, ChapterState.FAILED},
    ChapterState.RESOURCE_BLOCKED: {ChapterState.QUEUED, ChapterState.FAILED},
    ChapterState.FAILED: {ChapterState.QUEUED},  # retry
}


def can_transition(from_state: ChapterState, to_state: ChapterState) -> bool:
    return to_state in TRANSITIONS.get(from_state, set())


def assert_transition(from_state: ChapterState, to_state: ChapterState) -> None:
    if not can_transition(from_state, to_state):
        raise ValueError(f"Illegal transition: {from_state} -> {to_state}")
