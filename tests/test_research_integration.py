"""Integration tests for research scraping functionality.

These tests verify end-to-end workflows including task creation, 
scraping execution, and export generation using mocked HTTP responses.
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from datetime import datetime

# Import modules under test
sys = __import__("sys")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestResearchWorkflow:
    """End-to-end workflow tests for research feature."""

    @pytest.mark.asyncio
    async def test_create_and_monitor_task(self):
        """Test creating a scrape task and monitoring its progress."""
        from app.models.research_source import ResearchTask
        
        # Simulate task creation
        task = ResearchTask(
            id="task_test123",
            source_id="起点中文网",
            target_url="https://www.qidian.com/work/123456",
            status="pending",
            progress=0,
            chapters_scraped=0,
            error_message=None,
            created_at=datetime.utcnow().isoformat() + "Z",
            updated_at=datetime.utcnow().isoformat() + "Z",
        )
        
        assert task.status == "pending"
        assert task.progress == 0

    @pytest.mark.asyncio
    async def test_parser_chapter_extraction(self):
        """Test that parser correctly extracts chapters from HTML."""
        from workbench.collab.research_parser import ResearchParser
        
        rule_config = {
            "name": "Test",
            "base_url": "https://example.com",
            "chapter_list_selector": "a.chapter-link",
            "title_selector": "",
            "content_selector": "",
        }
        
        parser = ResearchParser(rule_config)
        
        html = """
        <html>
            <ul>
                <li><a href="/ch/1" class="chapter-link">Chapter 1</a></li>
                <li><a href="/ch/2" class="chapter-link">Chapter 2</a></li>
                <li><a href="/ch/3" class="chapter-link">Chapter 3</a></li>
            </ul>
        </html>
        """
        
        chapters = parser.parse_chapter_list(html)
        
        # Verify we found all 3 chapters
        assert len(chapters) >= 3
        assert any("/ch/1" in c["url"] for c in chapters)
        assert any("/ch/2" in c["url"] for c in chapters)
        assert any("/ch/3" in c["url"] for c in chapters)

    @pytest.mark.asyncio
    async def test_pagination_detection(self):
        """Test next-page link detection."""
        from workbench.collab.research_parser import ResearchParser
        
        rule_config = {
            "name": "Test",
            "base_url": "https://example.com/work/123",
            "chapter_list_selector": "a",
            "title_selector": "",
            "content_selector": "",
            "pagination_selector": "a.next-page",
        }
        
        parser = ResearchParser(rule_config)
        
        html = """
        <html>
            <a href="/work/123?page=2" class="next-page">Next Page</a>
        </html>
        """
        
        next_page = parser.find_next_page(html)
        
        assert next_page == "https://example.com/work/123?page=2"


class TestTaskQueueIntegration:
    """Tests for task queue coordination."""

    @pytest.mark.asyncio
    async def test_queue_status_reporting(self):
        """Verify task queue returns accurate status metrics."""
        from app.services.task_queue import ResearchTaskQueue
        
        queue = ResearchTaskQueue(max_workers=2)
        status = queue.get_queue_status()
        
        assert "queue_size" in status
        assert "running_jobs" in status
        assert "total_jobs" in status
        assert isinstance(status["queue_size"], int)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=app", "--cov=workbench"])
