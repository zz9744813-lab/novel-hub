"""Tests for research parser module."""

import pytest
from unittest.mock import Mock, patch

from workbench.collab.research_parser import ResearchParser


class TestResearchParser:
    """Test cases for ResearchParser class."""

    def test_initialization(self):
        """Test parser initialization with required selectors."""
        rule_config = {
            "name": "Test Source",
            "base_url": "https://example.com",
            "chapter_list_selector": "ul.chapters li a",
            "title_selector": "h1.title",
            "content_selector": "div.content",
        }
        
        parser = ResearchParser(rule_config)
        assert parser.base_url == "https://example.com"
        assert parser.chapter_list_selector == "ul.chapters li a"
        assert parser.title_selector == "h1.title"
        assert parser.content_selector == "div.content"

    def test_parse_chapter_list_with_html(self):
        """Test parsing chapter list from HTML."""
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
            </ul>
        </html>
        """
        
        chapters = parser.parse_chapter_list(html)
        
        # Should find at least 2 chapters
        assert len(chapters) >= 2
        assert any("/ch/1" in c["url"] for c in chapters)
        assert any("/ch/2" in c["url"] for c in chapters)

    def test_find_next_page_no_pagination(self):
        """Test pagination detection when no next page exists."""
        rule_config = {
            "name": "Test",
            "base_url": "https://example.com",
            "chapter_list_selector": "a",
            "title_selector": "",
            "content_selector": "",
            "pagination_selector": None,  # No pagination
        }
        
        parser = ResearchParser(rule_config)
        next_page = parser.find_next_page("<html><body>Content</body></html>")
        assert next_page is None

    def test_find_next_page_with_pagination(self):
        """Test detecting next page link."""
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
