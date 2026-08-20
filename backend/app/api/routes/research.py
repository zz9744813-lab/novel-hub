"""FastAPI REST API for research scraping functionality."""

import asyncio
import uuid
from datetime import datetime
from typing import Any
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "workbench" / "collab"))

from app.models.research_source import (
    OutputFormat,
    ResearchResult,
    ResearchTask,
    ResearchSourceRule,
)
from workbench.collab.research_scraper import ResearchScraper
from workbench.collab.research_exporter import ResearchExporter

router = APIRouter(prefix="/api/research", tags=["research"])

# Load sources from JSON config file
CONFIG_PATH = Path(__file__).parent.parent / "data" / "research_sources.json"


def _load_sources() -> dict[str, dict]:
    """Load source configurations from JSON file."""
    if not CONFIG_PATH.exists():
        return {}
    
    import json
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {src["name"]: src for src in data.get("sources", [])}
    except Exception as e:
        print(f"Error loading sources: {e}")
        return {}


source_configs: dict[str, dict] = _load_sources()

# SQLite database for task persistence
DB_PATH = Path(__file__).parent.parent / "data" / "research_cache.db"


def _init_database() -> None:
    """Initialize SQLite database for tasks."""
    import sqlite3
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS research_tasks (
                task_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_url TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                progress INTEGER DEFAULT 0,
                chapters_scraped INTEGER DEFAULT 0,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()


def _save_task_to_db(task: ResearchTask) -> None:
    """Save task state to database."""
    import sqlite3
    _init_database()
    
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""
            INSERT OR REPLACE INTO research_tasks 
            (task_id, source_id, target_url, status, progress, chapters_scraped, error_message, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task.id,
            task.source_id,
            task.target_url,
            task.status,
            task.progress,
            task.chapters_scraped,
            task.error_message,
            task.created_at,
            task.updated_at,
        ))


def _get_task_from_db(task_id: str) -> ResearchTask | None:
    """Get task from database by ID."""
    import sqlite3
    with sqlite3.connect(str(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT * FROM research_tasks WHERE task_id = ?",
            (task_id,)
        ).fetchone()
    
    if not row:
        return None
    
    return ResearchTask(
        id=row[0],
        source_id=row[1],
        target_url=row[2],
        status=row[3],
        progress=row[4],
        chapters_scraped=row[5],
        error_message=row[6],
        created_at=row[7],
        updated_at=row[8],
    )


class CreateTaskRequest(BaseModel):
    """Request to start a new research scraping task."""
    source_id: str
    target_url: str


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
    # Save to file
    import json
    data = {
        "version": "1.0",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sources": [s.model_dump() for s in source_configs.values()]
    }
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
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
            detail=f"Source '{request.source_id}' not found"
        )
    
    # Create task with UUID v4
    task_id = str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat() + "Z"
    
    task = ResearchTask(
        id=task_id,
        source_id=request.source_id,
        target_url=request.target_url,
        status="pending",
        progress=0,
        chapters_scraped=0,
        error_message=None,
        created_at=now,
        updated_at=now,
    )
    
    # Persist to database
    _save_task_to_db(task)
    
    # Start background scraping
    background_tasks.add_task(run_scraping_task, task)
    
    return task


async def run_scraping_task(task: ResearchTask) -> None:
    """Background worker that executes the actual scraping logic."""
    import asyncio
    
    try:
        source_config = source_configs.get(task.source_id)
        if not source_config:
            raise ValueError(f"Unknown source: {task.source_id}")
        
        scraper = ResearchScraper()
        
        # Update task to running
        task.status = "running"
        task.progress = 5
        task.updated_at = datetime.utcnow().isoformat() + "Z"
        _save_task_to_db(task)
        
        async def progress_callback(progress):
            task.chapters_scraped = progress.chapters_completed
            task.progress = progress.progress_percent
            task.error_message = "; ".join(progress.errors) if progress.errors else None
            task.updated_at = datetime.utcnow().isoformat() + "Z"
            _save_task_to_db(task)
        
        result_task = await scraper.scrape_task(
            source_config=source_config,
            start_url=task.target_url,
            progress_callback=progress_callback,
        )
        
        # Final update
        task.status = result_task.status
        task.progress = result_task.progress
        task.chapters_scraped = result_task.chapters_scraped
        task.error_message = result_task.error_message
        task.updated_at = datetime.utcnow().isoformat() + "Z"
        _save_task_to_db(task)
        
        await scraper.close()
        
    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        task.updated_at = datetime.utcnow().isoformat() + "Z"
        _save_task_to_db(task)


@router.get("/tasks/{task_id}", response_model=ResearchTask)
async def get_task_status(task_id: str) -> ResearchTask:
    """Get current status of a scraping task.
    
    Args:
        task_id: Task identifier
        
    Returns:
        Current task state
    """
    task = _get_task_from_db(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return task


@router.delete("/tasks/{task_id}")
async def cancel_task(task_id: str) -> dict[str, str]:
    """Cancel an active scraping task.
    
    Args:
        task_id: Task identifier
        
    Returns:
        Confirmation message
    """
    task = _get_task_from_db(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    task.status = "cancelled"
    task.updated_at = datetime.utcnow().isoformat() + "Z"
    _save_task_to_db(task)
    
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
    task = _get_task_from_db(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    if task.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot export incomplete task (status: {task.status})"
        )
    
    # TODO: Implement actual export logic here
    # For now, return placeholder response
    return ResearchResult(
        task_id=task_id,
        total_chapters=task.chapters_scraped,
        total_words=0,
        export_formats={format.value: f"/exports/{task_id}.{format.value}"},
        metadata={}
    )


@router.get("/tasks", response_model=list[ResearchTask])
async def list_tasks() -> list[ResearchTask]:
    """List all scraping tasks."""
    # In production: query all rows from database
    # For now, just return empty list
    return []
