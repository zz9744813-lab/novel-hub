"""Strict contract and deterministic quality gates for a novel production pack.

The pack deliberately contains only original planning material and abstract
style evidence.  Raw reference prose is never a field in this contract.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRecord(StrictModel):
    source_id: str
    title: str
    rights_basis: Literal["public_domain", "licensed", "user_owned", "user_authorized"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    allowed_use: list[str]
    forbidden_use: list[str]
    raw_reference_in_drafting_context: bool = False


class CandidateScore(StrictModel):
    premise: int = Field(ge=0, le=4)
    sustained_engine: int = Field(ge=0, le=4)
    character_capacity: int = Field(ge=0, le=4)
    change_space: int = Field(ge=0, le=4)
    differentiation: int = Field(ge=0, le=4)
    scene_variety: int = Field(ge=0, le=4)
    delivery_cost: int = Field(ge=0, le=4)
    creator_necessity: int = Field(ge=0, le=4)


class CandidateDirection(StrictModel):
    candidate_id: str
    name: str
    premise: str
    sustained_engine: str
    risk: str
    selected: bool = False
    scores: CandidateScore


class ReaderContract(StrictModel):
    opening_promise: str
    recurring_payoffs: list[str]
    long_questions: list[str]
    must_answer_by_ending: list[str]
    fairness_rules: list[str]
    emotional_temperature: str
    content_boundaries: list[str]
    must_not_become: list[str]


class BookSpec(StrictModel):
    title: str
    subtitle: str
    logline: str
    synopsis: str
    genre: str
    tags: list[str]
    tone: str
    audience: str
    target_chapters: int = Field(ge=12, le=1000)
    target_chars: int = Field(ge=100_000)
    chapter_target_chars: list[int] = Field(min_length=2, max_length=2)
    theme_question: str

    @model_validator(mode="after")
    def _validate_length_contract(self):
        lower, upper = self.chapter_target_chars
        if lower < 1000 or upper > 30_000 or lower > upper:
            raise ValueError("chapter_target_chars must be ordered within 1000..30000")
        minimum_total = self.target_chapters * lower
        maximum_total = self.target_chapters * upper
        if not minimum_total <= self.target_chars <= maximum_total:
            raise ValueError(
                "target_chars must fit target_chapters * chapter_target_chars range"
            )
        return self


class MechanismCard(StrictModel):
    mechanism_id: str
    name: str
    reader_effect: str
    preconditions: list[str]
    procedure: list[str]
    failure_modes: list[str]
    evidence_source_ids: list[str]
    confidence: Literal["low", "medium", "high"]


class StyleSpec(StrictModel):
    approved: bool
    source_ids: list[str]
    confidence: Literal["low", "medium", "high"]
    metric_vector: dict[str, Any]
    metric_ranges: dict[str, Any]
    narrative: dict[str, Any]
    dialogue: dict[str, Any]
    rhythm: dict[str, Any]
    emotion_expression: dict[str, Any]
    techniques: list[dict[str, Any]]
    scene_modes: dict[str, Any]
    tone_anchor: dict[str, Any]
    negative_fingerprint: list[str]


class CharacterAnchor(StrictModel):
    code: str = Field(max_length=32)
    anchor_type: str = Field(max_length=32)
    statement: str
    priority: float = Field(ge=0, le=1)
    rigidity: float = Field(ge=0, le=1)


class CharacterSpec(StrictModel):
    character_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    role: str
    public_identity: str
    self_story: str
    external_goal: str
    internal_need: str
    false_belief: str
    fear: str
    wound_or_origin: str
    secret: str
    contradiction: str
    default_strategy: str
    escalation_strategy: str
    line_they_will_not_cross: str
    line_crossing_price: str
    competence: str
    blind_spot: str
    agency_source: str
    arc_question: str
    behavior_evidence: list[str]
    voice: dict[str, Any]
    anchors: list[CharacterAnchor]


class RelationshipSpec(StrictModel):
    relationship_id: str
    from_character_id: str
    to_character_id: str
    relation_type: str
    surface_relation: str
    from_wants: str
    to_wants: str
    mutual_misunderstanding: str
    power_sources: list[str]
    debts_and_secrets: list[str]
    trust: float = Field(ge=-1, le=1)
    dependence: float = Field(ge=-1, le=1)
    fear: float = Field(ge=-1, le=1)
    desire: float = Field(ge=-1, le=1)
    start_chapter_no: int = Field(ge=1)


class WorldRuleSpec(StrictModel):
    rule_id: str
    category: str
    statement: str
    scope: str
    cost: str
    who_knows: list[str]
    who_benefits: list[str]
    exceptions: str
    violation_block: str
    is_hard: bool = True


class LocationSpec(StrictModel):
    location_id: str
    name: str
    description: str
    environment: str
    resources: list[str]
    rules: list[str]


class WorldSpec(StrictModel):
    setting_summary: str
    resource_constraints: list[str]
    rules: list[WorldRuleSpec]
    locations: list[LocationSpec]


class PlotThreadSpec(StrictModel):
    thread_id: str
    name: str
    description: str
    plant_chapter: int = Field(ge=1)
    planned_payoff_chapter: int = Field(ge=1)


class VolumeSpec(StrictModel):
    volume_no: int = Field(ge=1)
    title: str
    chapter_from: int = Field(ge=1)
    chapter_to: int = Field(ge=1)
    question: str
    primary_strategy: str
    strategy_failure: str
    midpoint_reframe: str
    irreversible_end_state: str
    next_problem: str
    themes: list[str]
    involved_character_ids: list[str]


class EventNodeSpec(StrictModel):
    event_id: str
    name: str
    chapter_no: int = Field(ge=1)
    initiator: str
    choice: str
    result: str
    state_delta: str
    new_debt: str
    opening: bool = False
    key_event: bool = True


class EventEdgeSpec(StrictModel):
    source: str
    target: str
    relation_type: Literal[
        "cause", "enable", "motivate", "reveal", "escalate", "echo", "payoff", "block"
    ]
    mechanism: str


class EventGraphSpec(StrictModel):
    nodes: list[EventNodeSpec]
    edges: list[EventEdgeSpec]


class ChapterSpec(StrictModel):
    chapter_no: int = Field(ge=1)
    volume_no: int = Field(ge=1)
    title: str
    active_driver: str
    involved_character_ids: list[str]
    goal: str
    opposition: str
    turn: str
    cost: str
    payoff: str
    new_question: str
    event_ids: list[str]
    plot_thread_ids: list[str]
    depends_on_chapters: list[int] = Field(default_factory=list)
    forbidden_outcomes: list[str]


class WritingConstraintSpec(StrictModel):
    constraint_id: str
    constraint_type: str
    title: str
    body: str
    priority: int = Field(ge=0, le=1000)
    is_hard: bool


class ProductionPack(StrictModel):
    schema_version: Literal["1.0"]
    pack_id: str
    revision: int = Field(ge=1)
    book: BookSpec
    reader_contract: ReaderContract
    candidates: list[CandidateDirection]
    sources: list[SourceRecord]
    mechanisms: list[MechanismCard]
    style: StyleSpec
    characters: list[CharacterSpec]
    relationships: list[RelationshipSpec]
    world: WorldSpec
    plot_threads: list[PlotThreadSpec]
    volumes: list[VolumeSpec]
    event_graph: EventGraphSpec
    chapters: list[ChapterSpec]
    writing_constraints: list[WritingConstraintSpec]
    reference_residue_denylist: list[str]

    def canonical_sha256(self) -> str:
        raw = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


class ValidationIssue(StrictModel):
    code: str
    message: str
    path: str | None = None


class ValidationReport(StrictModel):
    passed: bool
    pack_id: str
    revision: int
    pack_sha256: str
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    counts: dict[str, int]


class ProductionPackValidationError(ValueError):
    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__(
            "; ".join(f"{item.code}: {item.message}" for item in report.errors)
            or "production pack validation failed"
        )


def _unique(items: list[Any], field: str, label: str, errors: list[ValidationIssue]) -> set[str]:
    seen: set[str] = set()
    for index, item in enumerate(items):
        value = str(getattr(item, field, "") or "").strip()
        if not value:
            errors.append(ValidationIssue(code="MISSING_ID", message=f"missing {field}", path=f"{label}[{index}]"))
        elif value in seen:
            errors.append(ValidationIssue(code="DUPLICATE_ID", message=value, path=f"{label}[{index}]"))
        else:
            seen.add(value)
    return seen


def _creative_strings(pack: ProductionPack) -> list[tuple[str, str]]:
    # Source titles and the denylist are audit metadata, not drafting material.
    payload = pack.model_dump(mode="json", exclude={"sources", "reference_residue_denylist"})
    out: list[tuple[str, str]] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, str):
            out.append((path, value))
        elif isinstance(value, dict):
            for key, child in value.items():
                visit(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(payload, "")
    return out


def _causal_cycle(node_ids: set[str], edges: list[EventEdgeSpec]) -> list[str]:
    causal = {"cause", "enable", "motivate", "escalate", "block"}
    graph = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge.relation_type in causal and edge.source in graph and edge.target in graph:
            graph[edge.source].append(edge.target)
    visiting: set[str] = set()
    visited: set[str] = set()
    trail: list[str] = []

    def walk(node: str) -> list[str]:
        if node in visiting:
            start = trail.index(node) if node in trail else 0
            return trail[start:] + [node]
        if node in visited:
            return []
        visiting.add(node)
        trail.append(node)
        for nxt in graph[node]:
            cycle = walk(nxt)
            if cycle:
                return cycle
        trail.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node_id in graph:
        cycle = walk(node_id)
        if cycle:
            return cycle
    return []


def _reference_overlap_issues(
    pack: ProductionPack,
    reference_texts: list[str],
    *,
    ngram_size: int = 16,
) -> list[ValidationIssue]:
    if not reference_texts:
        return []
    owner: dict[str, str] = {}
    for path, value in _creative_strings(pack):
        normalized = re.sub(r"\s+", "", value)
        for index in range(max(0, len(normalized) - ngram_size + 1)):
            gram = normalized[index : index + ngram_size]
            if len(gram) == ngram_size:
                owner.setdefault(gram, path)
    if not owner:
        return []
    matched: dict[str, str] = {}
    for source in reference_texts:
        normalized = re.sub(r"\s+", "", source)
        for index in range(max(0, len(normalized) - ngram_size + 1)):
            gram = normalized[index : index + ngram_size]
            path = owner.get(gram)
            if path:
                matched.setdefault(path, hashlib.sha256(gram.encode("utf-8")).hexdigest()[:16])
    return [
        ValidationIssue(
            code="REFERENCE_NGRAM_OVERLAP",
            message=f"{ngram_size}-character overlap hash={digest}",
            path=path,
        )
        for path, digest in sorted(matched.items())
    ]


def validate_pack(
    pack: ProductionPack,
    *,
    reference_texts: list[str] | None = None,
) -> ValidationReport:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    source_ids = _unique(pack.sources, "source_id", "sources", errors)
    source_by_id = {source.source_id: source for source in pack.sources}
    source_hash_owners: dict[str, list[str]] = {}
    for source in pack.sources:
        source_hash_owners.setdefault(source.sha256, []).append(source.source_id)
    for digest, owners in source_hash_owners.items():
        if len(owners) > 1:
            errors.append(
                ValidationIssue(
                    code="DUPLICATE_SOURCE_HASH",
                    message=f"sha256={digest} source_ids={sorted(owners)}",
                )
            )
    character_ids = _unique(pack.characters, "character_id", "characters", errors)
    relationship_ids = _unique(pack.relationships, "relationship_id", "relationships", errors)
    rule_ids = _unique(pack.world.rules, "rule_id", "world.rules", errors)
    location_ids = _unique(pack.world.locations, "location_id", "world.locations", errors)
    thread_ids = _unique(pack.plot_threads, "thread_id", "plot_threads", errors)
    event_ids = _unique(pack.event_graph.nodes, "event_id", "event_graph.nodes", errors)
    _unique(pack.mechanisms, "mechanism_id", "mechanisms", errors)
    _unique(pack.writing_constraints, "constraint_id", "writing_constraints", errors)

    if len([candidate for candidate in pack.candidates if candidate.selected]) != 1:
        errors.append(ValidationIssue(code="CANDIDATE_SELECTION", message="exactly one candidate must be selected"))
    if len(pack.candidates) < 3:
        errors.append(ValidationIssue(code="CANDIDATE_COUNT", message="at least three directions are required"))

    for source in pack.sources:
        if source.raw_reference_in_drafting_context:
            errors.append(ValidationIssue(code="RAW_REFERENCE_CONTEXT", message=source.source_id))
        if not source.allowed_use or not source.forbidden_use:
            errors.append(ValidationIssue(code="SOURCE_BOUNDARY", message=source.source_id))
    if not pack.style.approved:
        errors.append(ValidationIssue(code="STYLE_NOT_APPROVED", message="style profile must be approved"))
    style_source_ids = set(pack.style.source_ids)
    unknown_style_sources = style_source_ids - source_ids
    if unknown_style_sources:
        errors.append(ValidationIssue(code="STYLE_SOURCE_REF", message=str(sorted(unknown_style_sources))))
    style_source_hashes = {
        source_by_id[source_id].sha256
        for source_id in style_source_ids
        if source_id in source_by_id
    }
    if len(style_source_hashes) < 3:
        warnings.append(
            ValidationIssue(
                code="SMALL_STYLE_CORPUS",
                message="fewer than three independent style sources; confidence must remain below high",
            )
        )
        if pack.style.confidence == "high":
            errors.append(ValidationIssue(code="STYLE_CONFIDENCE", message="small corpus cannot be high confidence"))

    for mechanism in pack.mechanisms:
        mechanism_source_ids = set(mechanism.evidence_source_ids)
        unknown = mechanism_source_ids - source_ids
        if unknown:
            errors.append(
                ValidationIssue(code="MECHANISM_SOURCE_REF", message=str(sorted(unknown)), path=mechanism.mechanism_id)
            )
        mechanism_source_hashes = {
            source_by_id[source_id].sha256
            for source_id in mechanism_source_ids
            if source_id in source_by_id
        }
        if len(mechanism_source_hashes) < 2 and mechanism.confidence == "high":
            errors.append(
                ValidationIssue(code="MECHANISM_CONFIDENCE", message="single-source mechanism cannot be high", path=mechanism.mechanism_id)
            )

    for rel in pack.relationships:
        refs = {rel.from_character_id, rel.to_character_id}
        if not refs <= character_ids or rel.from_character_id == rel.to_character_id:
            errors.append(ValidationIssue(code="RELATIONSHIP_REF", message=rel.relationship_id))
        if rel.start_chapter_no > pack.book.target_chapters:
            errors.append(
                ValidationIssue(code="RELATIONSHIP_CHAPTER", message=rel.relationship_id)
            )

    for thread in pack.plot_threads:
        if (
            thread.plant_chapter > thread.planned_payoff_chapter
            or thread.planned_payoff_chapter > pack.book.target_chapters
        ):
            errors.append(
                ValidationIssue(code="PLOT_THREAD_RANGE", message=thread.thread_id)
            )

    volume_nos = [volume.volume_no for volume in pack.volumes]
    if volume_nos != list(range(1, len(pack.volumes) + 1)):
        errors.append(ValidationIssue(code="VOLUME_SEQUENCE", message=str(volume_nos)))
    expected_from = 1
    for volume in pack.volumes:
        if volume.chapter_from != expected_from or volume.chapter_to < volume.chapter_from:
            errors.append(ValidationIssue(code="VOLUME_COVERAGE", message=volume.title))
        expected_from = volume.chapter_to + 1
        unknown = set(volume.involved_character_ids) - character_ids
        if unknown:
            errors.append(ValidationIssue(code="VOLUME_CHARACTER_REF", message=str(sorted(unknown)), path=volume.title))
    if expected_from - 1 != pack.book.target_chapters:
        errors.append(ValidationIssue(code="VOLUME_TARGET", message=f"covered={expected_from - 1}"))

    chapter_nos = [chapter.chapter_no for chapter in pack.chapters]
    expected_chapters = list(range(1, pack.book.target_chapters + 1))
    if chapter_nos != expected_chapters:
        errors.append(ValidationIssue(code="CHAPTER_SEQUENCE", message="chapters must be contiguous and ordered"))
    volume_by_no = {volume.volume_no: volume for volume in pack.volumes}
    for chapter in pack.chapters:
        volume = volume_by_no.get(chapter.volume_no)
        if volume is None or not volume.chapter_from <= chapter.chapter_no <= volume.chapter_to:
            errors.append(ValidationIssue(code="CHAPTER_VOLUME", message=str(chapter.chapter_no)))
        if chapter.active_driver not in character_ids:
            errors.append(ValidationIssue(code="CHAPTER_DRIVER", message=chapter.active_driver, path=str(chapter.chapter_no)))
        elif chapter.active_driver not in chapter.involved_character_ids:
            errors.append(
                ValidationIssue(
                    code="CHAPTER_DRIVER_NOT_INVOLVED",
                    message=chapter.active_driver,
                    path=str(chapter.chapter_no),
                )
            )
        unknown_chars = set(chapter.involved_character_ids) - character_ids
        unknown_events = set(chapter.event_ids) - event_ids
        unknown_threads = set(chapter.plot_thread_ids) - thread_ids
        if unknown_chars:
            errors.append(ValidationIssue(code="CHAPTER_CHARACTER_REF", message=str(sorted(unknown_chars)), path=str(chapter.chapter_no)))
        if unknown_events:
            errors.append(ValidationIssue(code="CHAPTER_EVENT_REF", message=str(sorted(unknown_events)), path=str(chapter.chapter_no)))
        if unknown_threads:
            errors.append(ValidationIssue(code="CHAPTER_THREAD_REF", message=str(sorted(unknown_threads)), path=str(chapter.chapter_no)))
        if any(dep >= chapter.chapter_no or dep < 1 for dep in chapter.depends_on_chapters):
            errors.append(ValidationIssue(code="CHAPTER_DEPENDENCY", message=str(chapter.depends_on_chapters), path=str(chapter.chapter_no)))
        if not all([chapter.goal, chapter.opposition, chapter.turn, chapter.cost, chapter.payoff, chapter.new_question]):
            errors.append(ValidationIssue(code="CHAPTER_CONTRACT", message="empty contract field", path=str(chapter.chapter_no)))
        if not chapter.forbidden_outcomes:
            errors.append(ValidationIssue(code="CHAPTER_FORBIDDEN", message="at least one forbidden outcome", path=str(chapter.chapter_no)))

    event_owners: dict[str, list[int]] = {event_id: [] for event_id in event_ids}
    for chapter in pack.chapters:
        for event_id in chapter.event_ids:
            if event_id in event_owners:
                event_owners[event_id].append(chapter.chapter_no)

    incoming: dict[str, set[str]] = {event_id: set() for event_id in event_ids}
    for edge in pack.event_graph.edges:
        if edge.source not in event_ids or edge.target not in event_ids:
            errors.append(ValidationIssue(code="EVENT_EDGE_REF", message=f"{edge.source}->{edge.target}"))
            continue
        incoming[edge.target].add(edge.relation_type)
    for node in pack.event_graph.nodes:
        if node.initiator not in character_ids:
            errors.append(ValidationIssue(code="EVENT_INITIATOR", message=node.initiator, path=node.event_id))
        if node.chapter_no > pack.book.target_chapters:
            errors.append(ValidationIssue(code="EVENT_CHAPTER", message=str(node.chapter_no), path=node.event_id))
        owners = event_owners.get(node.event_id) or []
        if node.chapter_no not in owners:
            errors.append(
                ValidationIssue(
                    code="EVENT_CHAPTER_PLACEMENT",
                    message=(
                        f"declared={node.chapter_no} referenced_by="
                        f"{owners}"
                    ),
                    path=node.event_id,
                )
            )
        if node.key_event and not node.opening and not (incoming[node.event_id] & {"cause", "enable", "motivate"}):
            errors.append(ValidationIssue(code="EVENT_CAUSAL_INBOUND", message=node.event_id))
    cycle = _causal_cycle(event_ids, pack.event_graph.edges)
    if cycle:
        errors.append(ValidationIssue(code="EVENT_CAUSAL_CYCLE", message=" -> ".join(cycle)))

    creative_blob = "\n".join(value for _, value in _creative_strings(pack))
    for residue in pack.reference_residue_denylist:
        if residue and residue in creative_blob:
            errors.append(ValidationIssue(code="REFERENCE_RESIDUE", message=residue))
    errors.extend(_reference_overlap_issues(pack, reference_texts or []))

    return ValidationReport(
        passed=not errors,
        pack_id=pack.pack_id,
        revision=pack.revision,
        pack_sha256=pack.canonical_sha256(),
        errors=errors,
        warnings=warnings,
        counts={
            "sources": len(source_ids),
            "characters": len(character_ids),
            "relationships": len(relationship_ids),
            "world_rules": len(rule_ids),
            "locations": len(location_ids),
            "plot_threads": len(thread_ids),
            "events": len(event_ids),
            "chapters": len(pack.chapters),
            "volumes": len(pack.volumes),
        },
    )


def load_and_validate_pack(
    path: str | Path,
    *,
    reference_texts: list[str] | None = None,
) -> tuple[ProductionPack, ValidationReport]:
    pack_path = Path(path).expanduser().resolve()
    if pack_path.is_dir():
        pack_path = pack_path / "pack.json"
    raw = json.loads(pack_path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and raw.get("bundle_version") == "1.0":
        root = pack_path.parent.resolve()

        def member(name: str) -> Path:
            candidate = (root / str(raw.get(name) or "")).resolve()
            if candidate.parent != root or not candidate.is_file():
                raise ValueError(f"invalid production-pack bundle member: {name}")
            return candidate

        core = json.loads(member("core").read_text(encoding="utf-8"))
        chapters = json.loads(member("chapters").read_text(encoding="utf-8"))
        if not isinstance(core, dict) or not isinstance(chapters, list):
            raise ValueError("production-pack bundle members have invalid roots")
        core["chapters"] = chapters
        raw = core
    pack = ProductionPack.model_validate(raw)
    report = validate_pack(pack, reference_texts=reference_texts)
    if not report.passed:
        raise ProductionPackValidationError(report)
    return pack, report
