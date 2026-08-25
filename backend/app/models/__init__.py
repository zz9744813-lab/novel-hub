"""Export all models for Alembic auto-detection."""
from app.models.base import Base
from app.models.tables import (
    Book, BookSetting, OutlineVersion, OutlineNode, OutlineDependency,
    ChapterTask, Chapter, ChapterVersion, Scene, Paragraph, ChapterStateEvent,
    ChapterRun, ChapterStepRun, ChapterDispatchOutbox,
    CharacterCard, CharacterStateEvent, CharacterStateSnapshot,
    WorldRule, PlotThread, RelationshipEvent, ItemEvent, TimelineEvent,
    StoryEvent, EntityAlias, SceneSearchDocument,
    MemoryL1ChapterLedger, MemoryL2StageSummary, MemoryL3VolumeSummary, MemoryL4StateSnapshot,
    StyleVoiceCard, StyleToneAnchor,
    QueryPlan, RetrievalRun, RetrievalCandidate, RetrievalJudgement,
    ReviewIssue, RewritePatch, DriftAuditReport,
    AgentRun, AgentRunOutput, LlmUsageEvent,
    HumanIntervention, PromptTemplate, TechniqueCard,
    # v7.4 models
    AgentModelBinding, ModelChangeLog, ModelRouteEvent, AgentContextPackage,
    ReferenceSample, GenreProfile, ResearchSession, ExternalResearchEvidence,
    # v8.0
    BookProfile, BookSource, ImportSession, ImportSessionEvent, ImportArtifact, ImportConflict,
    LocationCard, CharacterRelationship, OutlineVolume, WritingConstraint,
    PromptTemplateVersion, PromptTestRun,
    # v9.0 cognitive-causal
    CharacterCoreAnchor, SceneReasoningContract, StoryEventEdge,
    # v9.1 research production
    ResearchSource, ResearchTask, ResearchDocument, ResearchExport,
    # v9.2 research source certification
    ResearchSourceProbeRun, ResearchSourceVersion,
    # v9.2 style intelligence engine
    StyleProfile, ChapterStyleScore, StyleSampleSegment, SceneStyleContract,
    # v9.3 editorial learning loop
    EditorialReviewPolicy, EditorialRubricTemplate, EditorialReviewRound,
    EditorialAnnotation, EditorialFeedbackInsight, EditorialExperienceCard,
    EditorialPreferencePair, EditorialImprovementProposal,
    EditorialRegressionCase, EditorialExperiment,
    # v9.4 autonomous writing session
    WritingSession, WritingSessionEvent, SessionAdvanceOutbox,
    # v9.5 model autopilot
    ModelCatalog, ModelCapabilityProfile, ModelHealthProbe, ModelHealthSnapshot,
    ModelRoleScore, ModelRoutingPolicy, ModelRoutePlan, ModelAutoConfigRun,
)

__all__ = [
    "Base",
    "Book", "BookSetting", "OutlineVersion", "OutlineNode", "OutlineDependency",
    "ChapterTask", "Chapter", "ChapterVersion", "Scene", "Paragraph", "ChapterStateEvent",
    "ChapterRun", "ChapterStepRun", "ChapterDispatchOutbox",
    "CharacterCard", "CharacterStateEvent", "CharacterStateSnapshot",
    "WorldRule", "PlotThread", "RelationshipEvent", "ItemEvent", "TimelineEvent",
    "StoryEvent", "EntityAlias", "SceneSearchDocument",
    "MemoryL1ChapterLedger", "MemoryL2StageSummary", "MemoryL3VolumeSummary", "MemoryL4StateSnapshot",
    "StyleVoiceCard", "StyleToneAnchor",
    "QueryPlan", "RetrievalRun", "RetrievalCandidate", "RetrievalJudgement",
    "ReviewIssue", "RewritePatch", "DriftAuditReport",
    "AgentRun", "AgentRunOutput", "LlmUsageEvent",
    "HumanIntervention", "PromptTemplate", "TechniqueCard",
    "AgentModelBinding", "ModelChangeLog", "ModelRouteEvent", "AgentContextPackage",
    "ReferenceSample", "GenreProfile", "ResearchSession", "ExternalResearchEvidence",
    "BookProfile", "BookSource", "ImportSession", "ImportSessionEvent", "ImportArtifact", "ImportConflict",
    "LocationCard", "CharacterRelationship", "OutlineVolume", "WritingConstraint",
    "PromptTemplateVersion", "PromptTestRun",
    "CharacterCoreAnchor", "SceneReasoningContract", "StoryEventEdge",
    "ResearchSource", "ResearchTask", "ResearchDocument", "ResearchExport",
    "ResearchSourceProbeRun", "ResearchSourceVersion",
    "StyleProfile", "ChapterStyleScore", "StyleSampleSegment", "SceneStyleContract",
    "EditorialReviewPolicy", "EditorialRubricTemplate", "EditorialReviewRound",
    "EditorialAnnotation", "EditorialFeedbackInsight", "EditorialExperienceCard",
    "EditorialPreferencePair", "EditorialImprovementProposal",
    "EditorialRegressionCase", "EditorialExperiment",
    "WritingSession", "WritingSessionEvent", "SessionAdvanceOutbox",
    "ModelCatalog", "ModelCapabilityProfile", "ModelHealthProbe",
    "ModelHealthSnapshot", "ModelRoleScore", "ModelRoutingPolicy",
    "ModelRoutePlan",
]
