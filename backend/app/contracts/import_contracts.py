"""v8 Import multi-agent contracts — extra=forbid, strict."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)  # LLM JSON often needs soft coerce


class DocumentSection(StrictModel):
    start_block_id: str
    end_block_id: str
    type: str


class DocumentClassifierOutput(StrictModel):
    primary_type: str = "mixed_book_proposal"
    document_types: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    sections: list[DocumentSection] = Field(default_factory=list)


class SanitizeItem(StrictModel):
    block_id: str
    classification: Literal[
        "source_content",
        "assistant_chatter",
        "user_chatter",
        "duplicate_summary",
        "format_noise",
        "uncertain",
    ] = "source_content"
    action: Literal["keep", "exclude", "review"] = "keep"
    confidence: float = 0.5
    reason: str = ""


class SanitizeBatchOutput(StrictModel):
    items: list[SanitizeItem] = Field(default_factory=list)


class BookMetadataOutput(StrictModel):
    title: str | None = None
    subtitle: str | None = None
    logline: str | None = None
    synopsis: str | None = None
    genre: str | None = None
    tags: list[str] = Field(default_factory=list)
    tone: str | None = None
    themes: list[str] = Field(default_factory=list)
    core_loop: str | None = None
    planned_chapters: int | None = None
    confidence: float = 0.5


class WorldRuleItem(StrictModel):
    rule_key: str
    description: str
    category: str | None = None
    is_hard: bool = True


class LocationItem(StrictModel):
    name: str
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)


class WorldBibleOutput(StrictModel):
    world_summary: str | None = None
    rules: list[WorldRuleItem] = Field(default_factory=list)
    locations: list[LocationItem] = Field(default_factory=list)


class CharacterItem(StrictModel):
    temp_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    role: str | None = None
    description: str | None = None
    gender: str | None = None


class CharacterExtractorOutput(StrictModel):
    characters: list[CharacterItem] = Field(default_factory=list)


class RelationshipItem(StrictModel):
    from_temp_id: str
    to_temp_id: str
    relation_type: str
    description: str | None = None
    stage: str | None = None


class RelationshipExtractorOutput(StrictModel):
    relationships: list[RelationshipItem] = Field(default_factory=list)


class VolumeItem(StrictModel):
    volume_no: int
    title: str | None = None
    chapter_from: int | None = None
    chapter_to: int | None = None
    goal: str | None = None
    themes: list[str] = Field(default_factory=list)


class ChapterOutlineItem(StrictModel):
    chapter_no: int
    title: str | None = None
    goal: str = ""
    volume_no: int = 1
    required_beats: list[str] = Field(default_factory=list)
    forbidden_outcomes: list[str] = Field(default_factory=list)
    source_heading: str | None = None


class OutlineExtractorV2Output(StrictModel):
    volumes: list[VolumeItem] = Field(default_factory=list)
    chapters: list[ChapterOutlineItem] = Field(default_factory=list)
    declared_total_chapters: int | None = None
    notes: list[str] = Field(default_factory=list)


class PlotThreadItem(StrictModel):
    temp_id: str
    name: str
    description: str | None = None
    status: str = "open"
    plant_chapter: int | None = None


class PlotThreadExtractorOutput(StrictModel):
    threads: list[PlotThreadItem] = Field(default_factory=list)
    foreshadows: list[dict[str, Any]] = Field(default_factory=list)


class WritingRuleItem(StrictModel):
    constraint_type: str
    title: str
    body: str
    is_hard: bool = False
    scope_type: str = "book"
    priority: int = 50


class WritingRuleExtractorOutput(StrictModel):
    rules: list[WritingRuleItem] = Field(default_factory=list)


class ConsistencyIssue(StrictModel):
    code: str
    severity: Literal["warning", "blocking"] = "warning"
    message: str
    entity_type: str | None = None
    entity_temp_id: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)


class ImportConsistencyAuditorOutput(StrictModel):
    issues: list[ConsistencyIssue] = Field(default_factory=list)
    ok: bool = True


IMPORT_CONTRACTS: dict[str, type[BaseModel]] = {
    "document_classifier": DocumentClassifierOutput,
    "import_sanitizer": SanitizeBatchOutput,
    "book_metadata_extractor": BookMetadataOutput,
    "world_bible_extractor": WorldBibleOutput,
    "character_extractor": CharacterExtractorOutput,
    "relationship_extractor": RelationshipExtractorOutput,
    "outline_extractor_v2": OutlineExtractorV2Output,
    "plot_thread_extractor": PlotThreadExtractorOutput,
    "writing_rule_extractor": WritingRuleExtractorOutput,
    "import_consistency_auditor": ImportConsistencyAuditorOutput,
}
