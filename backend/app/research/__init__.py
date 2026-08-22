"""v9.1 Research production module (spec §16-§24).

Migrated from workbench/collab/research_* experimental code into the
production backend: PostgreSQL persistence, ARQ worker execution,
httpx-only HTTP layer, quality-validated parsing.
"""
from app.research.models import (
    ExtractionQuality,
    ResearchSourceConfig,
    ScrapedDocument,
)

__all__ = ["ExtractionQuality", "ResearchSourceConfig", "ScrapedDocument"]
