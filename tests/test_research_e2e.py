"""E2E integration tests for research scraping functionality.

Tests the complete workflow: task creation → scraping → progress monitoring → export → reference import.
Uses mock HTTP server responses to simulate external sites.
"""

import pytest
import asyncio
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "workbench" / "collab"))


class TestResearchWorkflow:
    """End-to-end tests for the research scraping feature."""

    @pytest.mark.asyncio
    async def test_create_scrape_task_with_mock_response(self):
        """Test creating a scrape task with mocked HTTP responses."""
        from fastapi.testclient import TestClient
        from app.main import app
        
        client = TestClient(app)
        
        # Mock the ResearchScraper class
        mock_scraper = MagicMock()
        mock_task = MagicMock()
        mock_task.status = "completed"
        mock_task.progress = 100
        mock_task.chapters_scraped = 5
        mock_task.error_message = None
        mock_scraper.scrape_task = asyncio.coroutine(lambda *args, **kwargs: mock_task)
        mock_scraper.close = asyncio.coroutine(lambda *args: None)
        
        with patch('app.api.routes.research.ResearchScraper', return_value=mock_scraper):
            response = client.post(
                "/api/research/tasks",
                json={
                    "source_id": "起点中文网示例",
                    "target_url": "https://example.com/novel/chapter-1"
                }
            )
            
            assert response.status_code == 200
            data = response.json()
            assert "id" in data
            assert data["status"] == "completed"
            assert data["chapters_scraped"] == 5

    @pytest.mark.asyncio
    async def test_get_task_status_from_database(self):
        """Test retrieving task status from SQLite database."""
        from fastapi.testclient import TestClient
        from app.main import app
        import sqlite3
        
        client = TestClient(app)
        
        # Create test task in DB directly
        db_path = Path(__file__).parent.parent / "data" / "research_cache.db"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO research_tasks 
                (task_id, source_id, target_url, status, progress, chapters_scraped, error_message, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "test_task_123",
                "起点中文网示例",
                "https://example.com/test",
                "running",
                45,
                3,
                None,
                "2026-08-20T10:00:00Z",
                "2026-08-20T10:05:00Z"
            ))
        
        # Retrieve task status
        response = client.get("/api/research/tasks/test_task_123")
        
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "test_task_123"
        assert data["status"] == "running"
        assert data["progress"] == 45
        
        # Cleanup
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("DELETE FROM research_tasks WHERE task_id = ?", ("test_task_123",))

    @pytest.mark.asyncio
    async def test_cancel_active_task(self):
        """Test cancelling an active scraping task."""
        from fastapi.testclient import TestClient
        from app.main import app
        import sqlite3
        
        client = TestClient(app)
        db_path = Path(__file__).parent.parent / "data" / "research_cache.db"
        
        # Create running task
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO research_tasks 
                (task_id, source_id, target_url, status, progress, chapters_scraped, error_message, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "cancel_test_456",
                "起点中文网示例",
                "https://example.com/test",
                "running",
                50,
                5,
                None,
                "2026-08-20T10:00:00Z",
                "2026-08-20T10:05:00Z"
            ))
        
        # Cancel task
        response = client.delete("/api/research/tasks/cancel_test_456")
        
        assert response.status_code == 200
        data = response.json()
        assert "cancelled" in data["message"]
        
        # Verify status updated
        response = client.get("/api/research/tasks/cancel_test_456")
        assert response.json()["status"] == "cancelled"
        
        # Cleanup
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("DELETE FROM research_tasks WHERE task_id = ?", ("cancel_test_456",))

    @pytest.mark.asyncio
    async def test_export_completed_task(self):
        """Test exporting completed task results."""
        from fastapi.testclient import TestClient
        from app.main import app
        import sqlite3
        
        client = TestClient(app)
        db_path = Path(__file__).parent.parent / "data" / "research_cache.db"
        
        # Create completed task
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO research_tasks 
                (task_id, source_id, target_url, status, progress, chapters_scraped, error_message, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "export_test_789",
                "起点中文网示例",
                "https://example.com/test",
                "completed",
                100,
                10,
                None,
                "2026-08-20T10:00:00Z",
                "2026-08-20T10:10:00Z"
            ))
        
        # Export task
        response = client.post("/api/research/tasks/export_test_789/export?format=txt")
        
        # Should return export result structure
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "export_test_789"
        assert data["total_chapters"] == 10
        assert "export_formats" in data
        
        # Cleanup
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("DELETE FROM research_tasks WHERE task_id = ?", ("export_test_789",))


class TestResearchToReferenceConverter:
    """Tests for the Reference Library conversion service."""

    def test_extract_genre_hint_from_chapters(self):
        """Test genre hint extraction heuristic."""
        from app.services.research_to_reference import ResearchToReferenceConverter
        
        converter = ResearchToReferenceConverter()
        
        # Fantasy content
        fantasy_chapters = [
            {"content": "The wizard cast a powerful spell and summoned a dragon."}
        ]
        genre = converter.extract_genre_hint(fantasy_chapters)
        assert genre == "奇幻"
        
        # Sci-fi content
        sci_fi_chapters = [
            {"content": "The robot traveled to another galaxy using alien technology."}
        ]
        genre = converter.extract_genre_hint(sci_fi_chapters)
        assert genre == "科幻"
        
        # Historical Chinese content
        ancient_chapters = [
            {"content": "将军率领军队攻入宫墙，皇帝站在城楼上。"}
        ]
        genre = converter.extract_genre_hint(ancient_chapters)
        assert genre == "古言"

    def test_create_sample_archive(self):
        """Test ZIP archive creation for research samples."""
        from app.services.research_to_reference import ResearchToReferenceConverter
        import zipfile
        
        converter = ResearchToReferenceConverter()
        
        chapters = [
            {"title": "Chapter 1", "content": "This is chapter one content..."},
            {"title": "Chapter 2", "content": "This is chapter two content..."},
        ]
        
        archive_path = converter.create_sample_archive("test_task", chapters, "book_123")
        
        # Verify archive exists
        assert archive_path.exists()
        assert archive_path.suffix == ".zip"
        
        # Verify contents
        with zipfile.ZipFile(archive_path, 'r') as zf:
            filenames = zf.namelist()
            assert len(filenames) == 2
            assert any("chapter_1" in f for f in filenames)
            assert any("chapter_2" in f for f in filenames)
        
        # Cleanup
        archive_path.unlink()

    def test_batch_convert_task_results(self):
        """Test batch conversion of task results to reference sample."""
        from app.services.research_to_reference import batch_convert_task_results
        
        chapters = [
            {"title": f"Chapter {i}", "content": f"Content for chapter {i}..."}
            for i in range(1, 6)
        ]
        
        result = batch_convert_task_results("task_abc", chapters, "book_xyz")
        
        assert result["task_id"] == "task_abc"
        assert result["book_id"] == "book_xyz"
        assert result["character_count"] > 0
        assert result["status"] == "ready"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=app", "--cov=workbench"])
