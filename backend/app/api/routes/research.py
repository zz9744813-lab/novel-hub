"""FastAPI REST API for research scraping functionality."""

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

sys = __import__("sys")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.research_source import (
    OutputFormat,
    ResearchResult,
    ResearchTask,
    ResearchSourceRule,
)


router = APIRouter(prefix="/api/research", tags=["research"])

# In-memory task registry (in production: Redis/PostgreSQL)
task_registry: dict[str, ResearchTask] = {}
source_configs: dict[str, dict] = {}


class CreateTaskRequest(BaseModel):
    """Request to start a new research scraping task."""

    source_id: str
    target_url: str


class ScrapingProgress(BaseModel):
    """Real-time progress updates during scraping."""

    task_id: str
    status: str
    progress_percent: int
    chapters_scraped: int
    error_message: str | None = None


@router.get("/sources", response_model=list[ResearchSourceRule])
async def list_research_sources() -> list[ResearchSourceRule]:
    """List all available research sources from loaded configuration.
    
    Returns:
        List of configured research sources with their selectors
    """
    return [
        ResearchSourceRule(**config) if isinstance(config, dict) else config
        for config in source_configs.values()
    ]


@router.post("/sources")
async def add_research_source(source: ResearchSourceRule) -> ResearchSourceRule:
    """Register a new research source rule.
    
    Args:
        source: New source configuration
        
    Returns:
        Registered source
    """
    source_configs[source.name] = source.model_dump()
    return source


@router.post("/tasks", response_model=ResearchTask)
async def create_scraping_task(
    request: CreateTaskRequest,
    background_tasks: BackgroundTasks,
) -> ResearchTask:
    """Create and start a new scraping task.
    
    Args:
        request: Task creation parameters
        background_tasks: FastAPI background task handler
        
    Returns:
        Newly created task
    """
    # Validate source exists
    if request.source_id not in source_configs:
        raise HTTPException(
            status_code=404,
            detail=f"Source '{request.source_id}' not found",
        )
    
    # Create task
    task = ResearchTask(
        id=f"task_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
        source_id=request.source_id,
        target_url=request.target_url,
        status="pending",
        progress=0,
        chapters_scraped=0,
        error_message=None,
        created_at=datetime.utcnow().isoformat() + "Z",
        updated_at=datetime.utcnow().isoformat() + "Z",
    )
    
    task_registry[task.id] = task
    
    # Start background scraping
    background_tasks.add_task(run_scraping_task, task)
    
    return task


async def run_scraping_task(task: ResearchTask) -> None:
    """Background worker that executes the actual scraping logic."""
    try:
        from workbench.collab.research_scraper import ResearchScraper
        from app.models.research_source import ResearchParser
        
        source_config = source_configs.get(task.source_id)
        if not source_config:
            raise ValueError(f"Unknown source: {task.source_id}")
        
        scraper = ResearchScraper()
        parser = ResearchParser(source_config)
        
        task.status = "running"
        task.progress = 5
        task.updated_at = datetime.utcnow().isoformat() + "Z"
        
        async def progress_callback(progress):
            task.chapters_scraped = progress.chapters_completed
            task.progress = progress.progress_percent
            task.error_message = "; ".join(progress.errors) if progress.errors else None
            task.updated_at = datetime.utcnow().isoformat() + "Z"
        
        result_task = await scraper.scrape_task(
            source_config=source_config,
            start_url=task.target_url,
            progress_callback=progress_callback,
        )
        
        # Update task from scraper result
        task.status = result_task.status
        task.progress = result_task.progress
        task.chapters_scraped = result_task.chapters_scraped
        task.error_message = result_task.error_message
        task.updated_at = datetime.utcnow().isoformat() + "Z"
        
        await scraper.close()
        
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        task.updated_at = datetime.utcnow().isoformat() + "Z"
        raise


@router.get("/tasks/{task_id}", response_model=ResearchTask)
async def get_task_status(task_id: str) -> ResearchTask:
    """Get current status of a scraping task.
    
    Args:
        task_id: Task identifier
        
    Returns:
        Current task state
    """
    if task_id not in task_registry:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return task_registry[task_id]


@router.delete("/tasks/{task_id}")
async def cancel_task(task_id: str) -> dict[str, str]:
    """Cancel an active scraping task.
    
    Args:
        task_id: Task identifier
        
    Returns:
        Confirmation message
    """
    if task_id not in task_registry:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task_registry[task_id].status = "cancelled"
    task_registry[task_id].updated_at = datetime.utcnow().isoformat() + "Z"
    
    return {"message": f"Task {task_id} cancelled"}


@router.post("/tasks/{task_id}/export")
async def export_task_results(
    task_id: str,
    format: OutputFormat = OutputFormat.TXT,
) -> ResearchResult:
    """Export completed task results to specified format.
    
    Args:
        task_id: Task to export
        format: Target format (txt/epub/pdf)
        
    Returns:
        Export result with file paths
    """
    if task_id not in task_registry:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task = task_registry[task_id]
    
    if task.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot export incomplete task (status: {task.status})",
        )
    
    # TODO: Implement actual export logic using ResearchExporter
    # This is a placeholder - full implementation in next phase
    
    return ResearchResult(
        task_id=task_id,
        total_chapters=task.chapters_scraped,
        total_words=0,  # Would be calculated from scraped content
        export_formats={format.value: f"/exports/{task_id}.{format.value}"},
        metadata={},
    )


@router.get("/tasks")
async def list_tasks() -> list[ResearchTask]:
    """List all scraping tasks (pagination supported)."""
    return list(task_registry.values())
