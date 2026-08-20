"""Data models for research source rules (so-novel inspired).

This module defines the data structures for parsing external novel websites
and encyclopedia sources using CSS/XPath selectors. Adapted from so-novel's
rule configuration format to fit NovelForge's Python/FastAPI stack.
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class OutputFormat(str, Enum):
    """Supported export formats for scraped content."""

    EPUB = "epub"
    PDF = "pdf"
    TXT = "txt"


class ResearchSourceRule(BaseModel):
    """Configuration for a single research source (website/encyclopedia).

    This model defines how to crawl and extract structured content from
    external sources like novel reading sites, wikis, or databases.
    
    Fields:
        name: Human-readable name of the source (e.g., "起点中文网")
        base_url: Root URL of the source site
        chapter_list_selector: CSS/XPath selector for chapter list page
        chapter_detail_selector: CSS/XPath selector for chapter detail page
        title_selector: Selector for chapter/title text
        content_selector: Selector for main content body
        pagination_selector: Selector for next page button/link (optional)
        output_format: Default export format (epub/pdf/txt)
        encoding: Character encoding hint (utf-8/gb2312/big5, default utf-8)
        rate_limit: Max requests per second (0 = no limit)
    """

    name: str = Field(..., description="Human-readable source name")
    base_url: str = Field(..., description="Base URL of the source site")
    
    # Selectors (CSS or XPath)
    chapter_list_selector: str = Field(
        ..., 
        description="Selector for listing chapters (can be absolute URL pattern)"
    )
    chapter_detail_selector: Optional[str] = Field(
        None, 
        description="Selector for individual chapter detail page (if different from list)"
    )
    title_selector: str = Field(
        ..., 
        description="Selector for extracting chapter/title text"
    )
    content_selector: str = Field(
        ..., 
        description="Selector for main content/body text"
    )
    pagination_selector: Optional[str] = Field(
        None, 
        description="Selector for next page button/link (omit if no pagination)"
    )
    
    # Export configuration
    output_format: OutputFormat = Field(
        default=OutputFormat.TXT, 
        description="Default export format"
    )
    
    # Meta configuration
    encoding: str = Field(
        default="utf-8", 
        description="Character encoding (utf-8/gb2312/big5)"
    )
    rate_limit: float = Field(
        default=0.5, 
        ge=0,
        le=10,
        description="Max requests per second (0 = no limit)"
    )
    
    # Additional metadata
    description: Optional[str] = Field(
        None, 
        description="Human-readable description of this source"
    )
    tags: Optional[list[str]] = Field(
        None, 
        description="Tags for categorizing sources"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "name": "起点中文网",
                "base_url": "https://www.qidian.com",
                "chapter_list_selector": "ul/li/a[@href]",
                "title_selector": "h1.title",
                "content_selector": "div.chapter-content",
                "pagination_selector": "a.next-page",
                "output_format": "txt",
                "encoding": "utf-8",
                "rate_limit": 1.0,
                "description": "Mainstream Chinese web novel platform",
                "tags": ["novel", "fiction", "chinese"]
            }
        }


class ScrapedChapter(BaseModel):
    """Represents a single scraped chapter/section."""

    id: str = Field(..., description="Unique identifier (URL hash)")
    title: str = Field(..., description="Chapter/section title")
    content: str = Field(..., description="Full chapter content text")
    url: str = Field(..., description="Original URL")
    order: int = Field(0, description="Chapter order number")
    word_count: int = Field(0, description="Estimated character count")


class ResearchTask(BaseModel):
    """Represents an active research scraping task."""

    id: str = Field(..., description="Unique task identifier (UUID)")
    source_id: str = Field(..., description="Reference to ResearchSourceRule.name")
    target_url: str = Field(..., description="Starting URL to scrape from")
    status: str = Field("pending", description="Status: pending/running/completed/failed")
    progress: int = Field(0, ge=0, le=100, description="Completion percentage")
    chapters_scraped: int = Field(0, description="Number of chapters extracted")
    error_message: Optional[str] = Field(None, description="Error details if failed")
    created_at: str = Field(..., description="ISO timestamp")
    updated_at: str = Field(..., description="ISO timestamp")
    
    class Config:
        json_schema_extra = {
            "example": {
                "id": "task_abc123",
                "source_id": "起点中文网",
                "target_url": "https://www.qidian.com/work/123456",
                "status": "running",
                "progress": 45,
                "chapters_scraped": 23,
                "error_message": None,
                "created_at": "2026-08-20T10:30:00Z",
                "updated_at": "2026-08-20T10:35:22Z"
            }
        }


class ResearchResult(BaseModel):
    """Final result after scraping + export."""

    task_id: str = Field(..., description="Reference to completed task")
    total_chapters: int = Field(..., description="Total chapters scraped")
    total_words: int = Field(..., description="Total word count")
    export_formats: dict[str, str] = Field(
        {...}, 
        description="Map of format → file path (for EPUB/PDF/TXT)"
    )
    metadata: dict[str, Any] = Field(
        {...}, 
        description="Additional metadata (author, logline, etc.)"
    )
