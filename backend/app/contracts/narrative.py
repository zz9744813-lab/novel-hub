"""v9.0 Cognitive-Causal Narrative Engine (CCNE) contracts.

Pydantic v2 models for:
- ChapterPlanProposal: richer planner output (fixes prompt/runtime schema gap)
- SceneContract: the compiled reasoning contract between Planner and DraftWriter
- Attribution / reaction evidence structures for StateExtractor
- Counterfactual audit results

Rules (spec §18):
- Compiled contracts are strict; planner proposals tolerate imperfect LLM output.
- Planner proposal is NOT canon. Only Finalizer commits StoryEvent/L4 (CC-01/CC-02).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NarrativeModel(BaseModel):
    """Compiled-structure base: strict types, no unknown keys."""

    model_config = ConfigDict(extra="forbid")


class ProposalModel(BaseModel):
    """LLM-facing base: tolerate unknown keys from imperfect model output."""

    model_config = ConfigDict(extra="allow")


# ── primitive value objects ─────────────────────────────────────────


Operator = Literal[">=", "<=", "==", "!=", ">", "<", "exists", "not_exists"]


class StatePredicate(NarrativeModel):
    """A checkable assertion over a nested narrative-state path."""

    path: str = Field(min_length=1)
    op: Operator = "exists"
    value: Any = None

    def describe(self) -> str:
        if self.op in ("exists", "not_exists"):
            return f"{self.op}({self.path})"
        return f"{self.path} {self.op} {json.dumps(self.value, ensure_ascii=False, default=str)}"


class ProvisionalEvent(ProposalModel):
    """Pre-draft event with provisional key P-{chapter}-{scene}-{seq} (spec §62)."""

    event_key: str
    actor_id: str | None = None
    action: str = Field(min_length=1)
    event_type: str | None = None
    involves: list[str] = Field(default_factory=list)
    is_public: bool | None = None
    hard_effects: list[StateDelta] = Field(default_factory=list)
    notes: str | None = None


EdgeRelation = Literal[
    "CAUSES",
    "ENABLES",
    "PREVENTS",
    "REVEALS",
    "UPDATES_BELIEF",
    "TRIGGERS_APPRAISAL",
    "MOTIVATES",
    "INTENDS",
    "ACHIEVES_GOAL",
    "FRUSTRATES_GOAL",
    "TEMPORAL_BEFORE",
    "CONTRADICTS",
]

EDGE_RELATIONS: tuple[str, ...] = (
    "CAUSES",
    "ENABLES",
    "PREVENTS",
    "REVEALS",
    "UPDATES_BELIEF",
    "TRIGGERS_APPRAISAL",
    "MOTIVATES",
    "INTENDS",
    "ACHIEVES_GOAL",
    "FRUSTRATES_GOAL",
    "TEMPORAL_BEFORE",
    "CONTRADICTS",
)


class CausalEdge(NarrativeModel):
    from_key: str = Field(alias="from")
    to_key: str = Field(alias="to")
    relation: EdgeRelation = "CAUSES"
    mode: Literal["hard", "soft"] = "soft"
    mechanism: str | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("relation", mode="before")
    @classmethod
    def _norm_relation(cls, v: Any) -> str:
        if v is None:
            return "CAUSES"
        s = str(v).upper().strip().replace(" ", "_")
        if s in EDGE_RELATIONS:
            return s  # type: ignore[return-value]
        # common aliases
        aliases = {
            "CAUSE": "CAUSES",
            "ENABLE": "ENABLES",
            "PREVENT": "PREVENTS",
            "REVEAL": "REVEALS",
            "BELIEF": "UPDATES_BELIEF",
            "APPRAISAL": "TRIGGERS_APPRAISAL",
            "MOTIVATE": "MOTIVATES",
            "INTEND": "INTENDS",
            "BEFORE": "TEMPORAL_BEFORE",
            "CONTRADICT": "CONTRADICTS",
        }
        return aliases.get(s, "CAUSES")  # type: ignore[return-value]

    @field_validator("mode", mode="before")
    @classmethod
    def _norm_mode(cls, v: Any) -> str:
        s = str(v or "soft").lower().strip()
        return "hard" if s in ("hard", "strict", "must") else "soft"


class PerceptionDelta(NarrativeModel):
    """Who perceived which event — the gate for legal knowledge."""

    character_id: str
    event_key: str
    channel: Literal[
        "saw", "heard", "was_told", "read", "inferred_from", "remembered", "missed"
    ] = "saw"
    detail: str | None = None


class BeliefDelta(NarrativeModel):
    character_id: str
    belief_key: str = Field(min_length=1)
    before: float | None = None
    after: float = Field(ge=-1.0, le=1.0)
    polarity: int = 1
    source_event_keys: list[str] = Field(default_factory=list)

    @field_validator("polarity", mode="before")
    @classmethod
    def _norm_polarity(cls, v: Any) -> int:
        if v is None:
            return 1
        if isinstance(v, (int, float)):
            return 1 if v >= 0 else -1
        s = str(v).lower()
        return -1 if s in ("negative", "neg", "-", "false", "no") else 1


class AgencyVector(NarrativeModel):
    self_: float = Field(default=0.0, alias="self", ge=0.0, le=1.0)
    other: float = Field(default=0.0, ge=0.0, le=1.0)
    circumstance: float = Field(default=0.0, ge=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class CharacterAppraisal(NarrativeModel):
    """OCC-inspired appraisal vector (spec §10). Narrative parameters, not psychology truth."""

    character_id: str
    event_key: str | None = None
    goal_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    goal_congruence: float = Field(default=0.0, ge=-1.0, le=1.0)
    novelty: float = Field(default=0.0, ge=0.0, le=1.0)
    certainty: float = Field(default=0.5, ge=0.0, le=1.0)
    controllability: float = Field(default=0.5, ge=0.0, le=1.0)
    agency: AgencyVector = Field(default_factory=AgencyVector)
    norm_compatibility: float = Field(default=0.0, ge=-1.0, le=1.0)
    relationship_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    information_gain: float = Field(default=0.0, ge=0.0, le=1.0)
    autonomy_threat: float = Field(default=0.0, ge=0.0, le=1.0)
    attachment_threat: float = Field(default=0.0, ge=0.0, le=1.0)


class VAD(NarrativeModel):
    valence: float = Field(default=0.0, ge=-1.0, le=1.0)
    arousal: float = Field(default=0.0, ge=-1.0, le=1.0)
    dominance: float = Field(default=0.0, ge=-1.0, le=1.0)

    def distance(self, other: "VAD") -> float:
        return (
            (self.valence - other.valence) ** 2
            + (self.arousal - other.arousal) ** 2
            + (self.dominance - other.dominance) ** 2
        ) ** 0.5

    def to_list(self) -> list[float]:
        return [self.valence, self.arousal, self.dominance]


ShockKind = Literal[
    "none", "surprise", "goal_damage", "attachment_threat", "physical_threat", "betrayal_reveal"
]


class AffectTransition(NarrativeModel):
    character_id: str
    from_vad: VAD | None = None
    to_vad: VAD = Field(default_factory=VAD)
    cause_event_keys: list[str] = Field(default_factory=list)
    shock: ShockKind = "none"
    shock_event_key: str | None = None
    derived_emotions: list[str] = Field(default_factory=list)


class IntentionContract(NarrativeModel):
    character_id: str
    action_intent: str = Field(min_length=1)
    support_anchor_ids: list[str] = Field(default_factory=list)
    support_belief_keys: list[str] = Field(default_factory=list)
    support_goal_keys: list[str] = Field(default_factory=list)
    support_event_keys: list[str] = Field(default_factory=list)
    weight: Literal["pivotal", "major", "minor"] = "major"
    attribution_status: Literal["supported", "unresolved"] = "unresolved"
    unresolved_reason: str | None = None


class StateDelta(NarrativeModel):
    path: str = Field(min_length=1)
    value: Any = None
    old_value: Any = None
    mode: Literal["hard", "soft"] = "soft"
    source_event_key: str | None = None


class ExpressionConstraint(NarrativeModel):
    """Spec §13: engine outputs tendencies, never fixed body cues."""

    character_id: str
    visibility: Literal["low", "medium", "high"] | None = None
    motor_tension: Literal["low", "medium", "high"] | None = None
    speech_control: Literal["low", "medium", "high"] | None = None
    speech_rate: Literal["slower", "normal", "faster"] | None = None
    attention_narrowing: Literal["low", "medium", "high"] | None = None
    approach_tendency: Literal["low", "medium", "high"] | None = None
    avoidance_tendency: Literal["low", "medium", "high"] | None = None
    aggression_tendency: Literal["contained", "medium", "high"] | None = None


# ── planner proposal (LLM output, tolerant) ─────────────────────────


class SceneProposal(ProposalModel):
    """Richer scene proposal — closes the prompt/runtime schema gap (spec §2.2)."""

    scene_no: int | None = None
    goal: str | None = None
    dramatic_goal: str | None = None
    conflict: str | None = None
    characters: list[Any] = Field(default_factory=list)
    pov_character_id: str | None = None
    location: str | None = None
    location_id: str | None = None
    info_release: str | None = None
    emotion_change: str | None = None
    exit_state: str | None = None
    target_word_count: int | None = None
    must_include: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)
    # v9 causal proposal (optional; compiler tolerates absence)
    provisional_events: list[ProvisionalEvent] = Field(default_factory=list)
    causal_edges: list[CausalEdge] = Field(default_factory=list)
    belief_deltas: list[BeliefDelta] = Field(default_factory=list)
    intentions: list[IntentionContract] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("scene_no", mode="before")
    @classmethod
    def _norm_scene_no(cls, v: Any) -> int | None:
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def effective_goal(self) -> str:
        return str(self.dramatic_goal or self.goal or "推进场景目标")


class ChapterPlanProposal(ProposalModel):
    """Planner output: a candidate causal path, NOT authoritative facts (spec §23.2)."""

    chapter_goal: str = Field(min_length=1)
    scenes: list[SceneProposal] = Field(min_length=1)
    required_beat_mapping: list[Any] = Field(default_factory=list)
    source: Literal["model", "deterministic_fallback"] | None = None
    blocked: bool | None = None
    block_reason: str | None = None


# ── compiled Scene Contract ─────────────────────────────────────────


class SceneContract(NarrativeModel):
    """The compiled reasoning contract between Planner and DraftWriter (spec §18).

    DraftWriter may freely choose literary execution but must not violate
    hard effects, knowledge boundaries, belief deltas and key exit_state.
    """

    scene_no: int
    dramatic_goal: str = Field(min_length=1)
    pov_character_id: str | None = None
    location_id: str | None = None

    relevant_entity_ids: list[str] = Field(default_factory=list)

    preconditions: list[StatePredicate] = Field(default_factory=list)
    provisional_events: list[ProvisionalEvent] = Field(default_factory=list)
    causal_edges: list[CausalEdge] = Field(default_factory=list)

    perceptions: list[PerceptionDelta] = Field(default_factory=list)
    belief_deltas: list[BeliefDelta] = Field(default_factory=list)
    appraisals: list[CharacterAppraisal] = Field(default_factory=list)
    affect_transitions: list[AffectTransition] = Field(default_factory=list)

    intentions: list[IntentionContract] = Field(default_factory=list)
    expected_effects: list[StateDelta] = Field(default_factory=list)

    expression_constraints: list[ExpressionConstraint] = Field(default_factory=list)

    must_realize: list[str] = Field(default_factory=list)
    must_not_assert: list[str] = Field(default_factory=list)

    exit_state: dict[str, Any] = Field(default_factory=dict)

    contract_hash: str | None = None

    def compute_hash(self) -> str:
        payload = self.model_dump(mode="json", by_alias=True, exclude={"contract_hash"})
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── state extractor v9: reaction evidence + attribution ─────────────


class ReactionEvidence(ProposalModel):
    """Observable reaction from finalized prose — facts only (spec §29.1)."""

    reaction_key: str = Field(min_length=1)
    character_id: str | None = None
    scene_no: int | None = None
    evidence_paragraph_key: str | None = None
    reaction_summary: str = Field(min_length=1)
    weight: Literal["pivotal", "major", "minor"] = "major"


class ReactionAttribution(ProposalModel):
    """Constrained attribution: may only reference provided IDs (spec §29.2)."""

    reaction_key: str = Field(min_length=1)
    cause_event_keys: list[str] = Field(default_factory=list)
    core_anchor_ids: list[str] = Field(default_factory=list)
    belief_keys: list[str] = Field(default_factory=list)
    goal_keys: list[str] = Field(default_factory=list)
    relationship_refs: list[str] = Field(default_factory=list)
    status: Literal["supported", "unresolved"] = "unresolved"
    reason: str | None = None

    def has_any_support(self) -> bool:
        return bool(
            self.core_anchor_ids
            or self.belief_keys
            or self.goal_keys
            or self.relationship_refs
            or self.cause_event_keys
        )


class StateExtractV9Contract(ProposalModel):
    """StateExtractor v9: facts / reaction evidence / attributions split."""

    events: list[Any] = Field(default_factory=list)
    conflicts: list[Any] = Field(default_factory=list)
    reaction_evidence: list[ReactionEvidence] = Field(default_factory=list)
    attributions: list[ReactionAttribution] = Field(default_factory=list)
    l1_chapter_ledger: dict[str, Any] | None = None


# ── validation report ───────────────────────────────────────────────


class ContractFinding(NarrativeModel):
    code: str = Field(min_length=1)
    severity: Literal["blocker", "major", "minor", "advisory"] = "major"
    detail: str = ""
    scene_no: int | None = None
    character_id: str | None = None
    path: str | None = None


class ContractValidationReport(NarrativeModel):
    ok: bool = True
    findings: list[ContractFinding] = Field(default_factory=list)

    def add(self, code: str, severity: str, detail: str = "", **meta: Any) -> None:
        self.findings.append(ContractFinding(code=code, severity=severity, detail=detail, **meta))
        if severity in ("blocker", "major"):
            self.ok = False

    def merge(self, other: "ContractValidationReport") -> None:
        """Absorb another report's findings into this one."""
        self.findings.extend(other.findings)
        if not other.ok:
            self.ok = False

    @property
    def blockers(self) -> list[ContractFinding]:
        return [f for f in self.findings if f.severity == "blocker"]

    @property
    def majors(self) -> list[ContractFinding]:
        return [f for f in self.findings if f.severity == "major"]

    @property
    def advisories(self) -> list[ContractFinding]:
        return [f for f in self.findings if f.severity in ("minor", "advisory")]


# ── counterfactual audit ────────────────────────────────────────────


class CounterfactualFinding(NarrativeModel):
    removed_event_key: str
    checked_target_key: str
    support_after_removal: Literal["none", "remaining", "sufficient"]
    classification: Literal[
        "necessary_support", "contributing_support", "motivation_redundancy", "false_causal_emphasis"
    ]
    remaining_support_keys: list[str] = Field(default_factory=list)
    detail: str = ""


class CounterfactualReport(NarrativeModel):
    ok: bool = True
    findings: list[CounterfactualFinding] = Field(default_factory=list)
    audited_events: list[str] = Field(default_factory=list)
