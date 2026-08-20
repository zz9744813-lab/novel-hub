"""Service to convert Research task results into Reference samples."""

import zipfile
import os
from pathlib import Path
from datetime import datetime
from typing import Any

# Add parent directory for imports
sys = __import__("sys")
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "workbench" / "collab"))


class ResearchToReferenceConverter:
    """Converts scraped research data into Reference samples."""

    def __init__(self, reference_root: Path | None = None):
        """Initialize converter.
        
        Args:
            reference_root: Root directory for reference files (default: data/references)
        """
        self.reference_root = reference_root or Path(__file__).parent.parent / "data" / "references"
        self.reference_root.mkdir(parents=True, exist_ok=True)

    def extract_genre_hint(self, chapters: list[dict]) -> str | None:
        """Extract genre hint from first chapter content using basic heuristics.
        
        TODO: Replace with proper NLP/bert model in production
        
        Returns:
            Genre hint string or None if not determinable
        """
        if not chapters:
            return None
        
        first_chapter = chapters[0].get("content", "")
        if not first_chapter:
            return None
        
        # Basic keyword-based heuristic (placeholder)
        genre_keywords = {
            "奇幻": ["magic", "spell", "wizard", "dragon"],
            "科幻": ["robot", "space", "alien", "technology"],
            "古言": ["古代", "皇帝", "将军", "宫墙"],
            "现实": ["都市", "职场", "生活", "工作"],
        }
        
        text_lower = first_chapter.lower()
        for genre, keywords in genre_keywords.items():
            if any(kw in text_lower for kw in keywords):
                return genre
        
        return None  # Could not determine genre
    
    def create_sample_archive(self, task_id: str, chapters: list[dict], book_id: str) -> Path:
        """Create ZIP archive of scraped chapters.
        
        Args:
            task_id: Source task identifier
            chapters: List of scraped chapter dicts
            book_id: Target book ID
            
        Returns:
            Path to created ZIP file
        """
        filename = f"research_{book_id}_{task_id}.zip"
        filepath = self.reference_root / filename
        
        with zipfile.ZipFile(filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for i, chapter in enumerate(chapters, 1):
                chapter_filename = f"chapter_{i:04d}_{chapter.get('title', 'untitled')[:50]}.txt"
                chapter_text = chapter.get("content", "")
                zipf.writestr(chapter_filename, chapter_text)
        
        return filepath

    def save_reference_metadata(self, book_id: str, sample_file: Path, 
                                  total_words: int, genre_hint: str | None) -> dict[str, Any]:
        """Save reference metadata record.
        
        Args:
            book_id: Book identifier
            sample_file: Path to sample ZIP
            total_words: Total character count
            genre_hint: Extracted genre hint
            
        Returns:
            Metadata dict saved to database (simulated)
        """
        metadata = {
            "book_id": book_id,
            "filename": sample_file.name,
            "character_count": total_words,
            "genre_hint": genre_hint,
            "uploaded_at": datetime.utcnow().isoformat() + "Z",
            "status": "ready",
        }
        
        # TODO: Save to actual database
        print(f"Metadata would be saved: {metadata}")
        
        return metadata

    def convert(self, task_id: str, chapters: list[dict], book_id: str) -> dict[str, Any]:
        """Main conversion method.
        
        Args:
            task_id: Source task identifier
            chapters: List of scraped chapter dicts  
            book_id: Target book ID
            
        Returns:
            Created ReferenceSample dict
        """
        # Extract metadata
        total_words = sum(len(c.get("content", "")) for c in chapters)
        genre_hint = self.extract_genre_hint(chapters)
        
        # Create archive
        archive_path = self.create_sample_archive(task_id, chapters, book_id)
        
        # Save metadata
        metadata = self.save_reference_metadata(book_id, archive_path, total_words, genre_hint)
        
        # Return simulated ReferenceSample
        return {
            "id": str(datetime.now().timestamp()),
            "book_id": book_id,
            "filename": archive_path.name,
            "character_count": total_words,
            "genre_hint": genre_hint,
            "status": "ready",
            "uploaded_at": datetime.utcnow().isoformat() + "Z",
        }


def batch_convert_task_results(task_id: str, chapters: list[dict], book_id: str) -> dict[str, Any]:
    """Convenience function for batch conversion.
    
    This will be used when importing completed research tasks into ReferencesLibraryPage.
    """
    converter = ResearchToReferenceConverter()
    return converter.convert(task_id, chapters, book_id)
