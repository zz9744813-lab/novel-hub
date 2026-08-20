"""Research parser module - CSS/XPath selector engine for content extraction.

This module provides the core parsing logic for scraping structured content
from external websites using CSS selectors and XPath expressions.
"""

import re
from html.parser import HTMLParser
from typing import Optional

from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel


class ParseResult(BaseModel):
    """Result of parsing a single HTML document."""

    title: Optional[str] = None
    content: str = ""
    links: list[str] = []
    errors: list[str] = []


class ElementParser(HTMLParser):
    """Custom HTML parser to extract text from specific elements."""

    def __init__(self):
        super().__init__()
        self.current_text = ""
        self.in_target_element = False
        self.target_tag: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]):
        if tag == self.target_tag:
            self.in_target_element = True
            self.current_text = ""

    def handle_endtag(self, tag: str):
        if tag == self.target_tag:
            self.in_target_element = False

    def handle_data(self, data: str):
        if self.in_target_element:
            self.current_text += data


def parse_css_selector(selector: str) -> tuple[str, dict[str, str]]:
    """Parse simple CSS selector into tag + attributes.

    Examples:
        "div.content" -> ("div", {"class": "content"})
        "ul/li/a" -> Not supported (complex paths)
        "h1.title" -> ("h1", {"class": "title"})

    Note: This is a basic parser; full CSS selector support requires lxml/beautifulsoup4
    """
    # Extract tag name
    if "." in selector:
        parts = selector.split(".", 1)
        tag = parts[0] or "*"
        attr_name, attr_value = parts[1].split("=", 1) if "=" in parts[1] else ("class", parts[1])
        attr_value = attr_value.strip('"\'')
        return tag, {attr_name: attr_value}
    
    if "[" in selector:
        match = re.match(r"(\w+)\[([^\]=]+)=([^\]]+)\]", selector)
        if match:
            tag = match.group(1) or "*"
            attr_name, attr_value = match.group(2), match.group(3).strip('"\'')
            return tag, {attr_name: attr_value}
    
    return selector, {}


def find_elements_by_css(html: str, selector: str, limit: int = 100) -> list[Tag]:
    """Find elements matching a CSS selector using BeautifulSoup."""
    soup = BeautifulSoup(html, "html.parser")
    
    try:
        # Direct CSS selector support (BS4 built-in)
        elements = soup.select(selector)[:limit]
        return [el for el in elements if isinstance(el, Tag)]
        
    except Exception as e:
        # Fallback: simple tag search
        tag, attrs = parse_css_selector(selector)
        elements = soup.find_all(tag, attrs)
        return [el for el in elements[:limit] if isinstance(el, Tag)]


def extract_text_from_html(html: str, encoding: str = "utf-8") -> str:
    """Extract plain text from HTML, handling common encoding issues."""
    # Try multiple encodings
    for enc in [encoding, "utf-8", "gb2312", "big5", "latin-1"]:
        try:
            soup = BeautifulSoup(html.encode(enc).decode(enc), "html.parser")
            return soup.get_text(separator="\n", strip=True)
        except (UnicodeDecodeError, LookupError):
            continue
    
    # Last resort: replace non-ASCII
    clean_html = re.sub(rb"[^\x00-\x7F]+", b" ", html.encode("ascii", "ignore"))
    return BeautifulSoup(clean_html.decode("ascii"), "html.parser").get_text()


class ResearchParser:
    """Main parser class for extracting structured content from web pages."""

    def __init__(self, rule_config: dict):
        """Initialize with rule configuration.

        Args:
            rule_config: ResearchSourceRule model dict with selectors
        """
        self.rule = rule_config
        self.base_url = rule_config.get("base_url", "")
        self.encoding = rule_config.get("encoding", "utf-8")
        
        # Required selectors
        self.chapter_list_selector = rule_config["chapter_list_selector"]
        self.title_selector = rule_config["title_selector"]
        self.content_selector = rule_config["content_selector"]
        
        # Optional selectors
        self.pagination_selector = rule_config.get("pagination_selector")
        self.detail_selector = rule_config.get("chapter_detail_selector")
        
        self.errors: list[str] = []

    def parse_chapter_list(self, html: str) -> list[dict[str, str]]:
        """Parse chapter listing page to extract individual chapter URLs/titles.

        Returns:
            List of {url, title} dicts
        """
        results = []
        soup = BeautifulSoup(html, "html.parser")
        
        try:
            # Find chapter list items
            list_items = find_elements_by_css(html, self.chapter_list_selector)
            
            for item in list_items:
                # Extract URL
                url = item.get("href") or item.get("src")
                if not url:
                    # Try nested <a> tags
                    link_tag = item.find("a")
                    if link_tag:
                        url = link_tag.get("href")
                
                # Resolve relative URLs
                if url and not url.startswith("http"):
                    url = self.base_url.rstrip("/") + "/" + url.lstrip("/")
                
                # Extract title
                title = item.get_text(strip=True)
                if not title:
                    title_elem = item.find(["h1", "h2", "h3", "b", "strong"])
                    if title_elem:
                        title = title_elem.get_text(strip=True)
                
                if url and title:
                    results.append({"url": url, "title": title})
                    
        except Exception as e:
            self.errors.append(f"Failed to parse chapter list: {e}")
        
        return results[:100]  # Limit to prevent excessive requests

    def parse_chapter_detail(self, html: str, url: str) -> Optional[dict[str, str]]:
        """Parse individual chapter detail page.

        Returns:
            {url, title, content} dict or None if failed
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
            
            # Extract title
            title_elems = find_elements_by_css(html, self.title_selector)
            title = ""
            if title_elems:
                title = title_elems[0].get_text(strip=True)
            
            # Extract content
            content_elems = find_elements_by_css(html, self.content_selector)
            content = ""
            if content_elems:
                # Join all matching elements
                content_parts = [elem.get_text(separator="\n\n", strip=True) for elem in content_elems]
                content = "\n\n".join(content_parts)
            else:
                # Fallback: entire body text
                body = soup.find("body")
                if body:
                    content = extract_text_from_html(str(body), self.encoding)
            
            return {
                "url": url,
                "title": title,
                "content": content,
            }
            
        except Exception as e:
            self.errors.append(f"Failed to parse chapter detail '{url}': {e}")
            return None

    def find_next_page(self, html: str) -> Optional[str]:
        """Find next page URL for pagination traversal."""
        if not self.pagination_selector:
            return None
        
        try:
            soup = BeautifulSoup(html, "html.parser")
            next_elem = soup.select_one(self.pagination_selector)
            
            if not next_elem:
                return None
            
            url = next_elem.get("href") or next_elem.get("src")
            if url and not url.startswith("http"):
                url = self.base_url.rstrip("/") + "/" + url.lstrip("/")
            
            return url
            
        except Exception:
            return None

    def batch_parse(self, urls: list[str]) -> list[dict[str, str]]:
        """Parse multiple URLs concurrently (placeholder for async implementation).
        
        In practice, this will be implemented with asyncio.gather() in research_scraper.py
        """
        results = []
        self.errors = []
        
        # Placeholder: sequential parsing (async version in scraper)
        for url in urls[:50]:  # Limit for demo
            # In production: await fetch(url) with aiohttp/httpx
            pass
        
        return results
