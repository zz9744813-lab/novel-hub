"""Async research scraper - concurrent content extraction engine.

This module provides asynchronous HTTP crawling with rate limiting, encoding detection,
and parallel chapter scraping capabilities based on ResearchSourceRule configurations.
"""

import asyncio
import hashlib
import json
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

import aiohttp
import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.research_source import (
    OutputFormat,
    ResearchParser,
    ScrapedChapter,
    ResearchTask,
)


class ScrapeProgress(BaseModel):
    """Progress tracking during scraping."""

    task_id: str
    current_url: str
    chapters_found: int
    chapters_completed: int
    errors: list[str]
    progress_percent: int


class ResearchScraper:
    """Async web scraper for research source rules."""

    def __init__(self, api_key: str | None = None):
        """Initialize scraper with optional API key.
        
        Args:
            api_key: Optional authentication key for external services
        """
        self.api_key = api_key
        self.parser_cache: dict[str, ResearchParser] = {}
        self.task_db_path = Path(__file__).parent.parent / "data" / "research_cache.db"
        self.session: httpx.AsyncClient | None = None
        
    async def _get_session(self) -> httpx.AsyncClient:
        """Get or create async HTTP session."""
        if self.session is None or self.session.is_closed:
            headers = {
                "User-Agent": (
                    "NovelForge Research Scraper/1.0 (+https://novelforge.local)"
                ),
                "Accept": "text/html,application/xhtml+xml",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            self.session = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
                headers=headers,
            )
        return self.session
    
    def _init_task_database(self) -> None:
        """Initialize SQLite database for task state persistence."""
        self.task_db_path.parent.mkdir(parents=True, exist_ok=True)
        
        with sqlite3.connect(str(self.task_db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_tasks (
                    task_id TEXT PRIMARY KEY,
                    source_id TEXT,
                    target_url TEXT,
                    status TEXT,
                    progress INTEGER,
                    chapters_scraped INTEGER,
                    error_message TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scraped_chapters (
                    id TEXT PRIMARY KEY,
                    task_id TEXT,
                    title TEXT,
                    url TEXT,
                    word_count INTEGER,
                    created_at TEXT,
                    FOREIGN KEY(task_id) REFERENCES research_tasks(task_id)
                )
            """)
            conn.commit()
    
    def _update_task_status(
        self, 
        task_id: str, 
        status: str, 
        progress: int,
        chapters: int,
        error: str | None = None
    ) -> None:
        """Update task state in database."""
        now = datetime.utcnow().isoformat() + "Z"
        
        with sqlite3.connect(str(self.task_db_path)) as conn:
            conn.execute(
                """UPDATE research_tasks SET status=?, progress=?, chapters_scraped=?, error_message=?, updated_at=? WHERE task_id=?""",
                (status, progress, chapters, error, now, task_id),
            )
            conn.commit()
    
    def _save_chapter(self, task_id: str, chapter: ScrapedChapter) -> None:
        """Save a scraped chapter to database."""
        with sqlite3.connect(str(self.task_db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO scraped_chapters VALUES (?, ?, ?, ?, ?, ?)""",
                (chapter.id, task_id, chapter.title, chapter.url, chapter.word_count, chapter.created_at or datetime.utcnow().isoformat() + "Z"),
            )
            conn.commit()
    
    async def scrape_task(
        self, 
        source_config: dict, 
        start_url: str,
        progress_callback: Callable[[ScrapeProgress], None] | None = None
    ) -> ResearchTask:
        """Execute a full scraping task from start URL.
        
        Args:
            source_config: ResearchSourceRule configuration dict
            start_url: Starting point for scraping
            progress_callback: Optional callback for progress updates
            
        Returns:
            Completed ResearchTask with results
        """
        # Initialize components
        await self._get_session()
        self._init_task_database()
        
        # Create new task
        task_id = str(uuid.uuid4())[:8]
        task = ResearchTask(
            id=task_id,
            source_id=source_config.get("name", "Unknown"),
            target_url=start_url,
            status="pending",
            progress=0,
            chapters_scraped=0,
            error_message=None,
            created_at=datetime.utcnow().isoformat() + "Z",
            updated_at=datetime.utcnow().isoformat() + "Z",
        )
        
        try:
            # Update status
            self._update_task_status(task_id, "running", 0, 0)
            
            # Get parser
            parser = ResearchParser(source_config)
            self.parser_cache[task_id] = parser
            
            # Start scraping
            all_chapters: list[ScrapedChapter] = []
            visited_urls: set[str] = set()
            queue: list[str] = [start_url]
            max_pages = 500  # Safety limit
            page_count = 0
            
            while queue and page_count < max_pages:
                current_url = queue.pop(0)
                
                if current_url in visited_urls:
                    continue
                
                visited_urls.add(current_url)
                page_count += 1
                
                # Fetch page
                try:
                    async with self.session.get(current_url) as resp:
                        html = resp.text
                        encoding = resp.encoding or source_config.get("encoding", "utf-8")
                        
                except Exception as e:
                    parser.errors.append(f"Failed to fetch {current_url}: {e}")
                    continue
                
                # Parse chapter list or detail page
                chapters = parser.parse_chapter_list(html)
                
                if chapters:
                    # Found chapter links - add to queue
                    for chapter_info in chapters:
                        if chapter_info["url"] not in visited_urls:
                            queue.append(chapter_info["url"])
                            
                    # Also parse current page as a chapter if it has content
                    detail_result = parser.parse_chapter_detail(html, current_url)
                    if detail_result:
                        all_chapters.append(
                            ScrapedChapter(
                                id=hashlib.md5(detail_result["url"].encode()).hexdigest(),
                                title=detail_result.get("title", ""),
                                content=detail_result["content"],
                                url=detail_result["url"],
                                order=len(all_chapters),
                                word_count=len(detail_result["content"]),
                            )
                        )
                    
                    # Find next page for pagination
                    next_page = parser.find_next_page(html)
                    if next_page and next_page not in visited_urls:
                        queue.append(next_page)
                else:
                    # Direct chapter detail page - extract content
                    result = parser.parse_chapter_detail(html, current_url)
                    if result:
                        all_chapters.append(
                            ScrapedChapter(
                                id=hashlib.md5(result["url"].encode()).hexdigest(),
                                title=result.get("title", ""),
                                content=result["content"],
                                url=result["url"],
                                order=len(all_chapters),
                                word_count=len(result["content"]),
                            )
                        )
                
                # Report progress
                completed = len(all_chapters)
                remaining = len(queue)
                total = completed + remaining
                progress_pct = int((completed / max(total, 1)) * 100)
                
                progress = ScrapeProgress(
                    task_id=task_id,
                    current_url=current_url,
                    chapters_found=len(queue),
                    chapters_completed=completed,
                    errors=parser.errors.copy(),
                    progress_percent=progress_pct,
                )
                
                if progress_callback:
                    progress_callback(progress)
                
                # Update DB
                self._update_task_status(task_id, "running", progress_pct, completed)
                
                # Rate limiting
                rate_limit = source_config.get("rate_limit", 0.5)
                if rate_limit > 0:
                    await asyncio.sleep(1.0 / rate_limit)
            
            # Mark complete
            self._update_task_status(task_id, "completed", 100, len(all_chapters))
            
            # Save chapters to DB
            for chapter in all_chapters:
                self._save_chapter(task_id, chapter)
            
            # Update task object
            task.status = "completed"
            task.progress = 100
            task.chapters_scraped = len(all_chapters)
            
            return task
            
        except Exception as e:
            error_msg = str(e)
            self._update_task_status(task_id, "failed", 0, 0, error_msg)
            task.status = "failed"
            task.error_message = error_msg
            return task
    
    async def close(self) -> None:
        """Close HTTP session."""
        if self.session and not self.session.is_closed:
            await self.session.aclose()


# Convenience function for quick usage
async def scrape_source(rule: dict, url: str) -> ResearchTask:
    """Quick wrapper to scrape a single source."""
    scraper = ResearchScraper()
    try:
        task = await scraper.scrape_task(rule, url)
        return task
    finally:
        await scraper.close()
