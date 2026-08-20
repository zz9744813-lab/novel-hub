"""Research content exporter - generates EPUB/PDF/TXT documents from scraped data."""

import sys
from pathlib import Path
from typing import Any

try:
    from ebooklib import epub
    from bs4 import BeautifulSoup
except ImportError:
    print("Warning: ebooklib not installed. Some export formats will be disabled.")
    epub = None

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.research_source import ResearchTask, ScrapedChapter


class ResearchExporter:
    """Export scraped research results to standard document formats."""

    def __init__(self, output_dir: Path | None = None):
        """Initialize exporter.
        
        Args:
            output_dir: Directory for exported files (default: data/research_exports)
        """
        self.output_dir = output_dir or Path(__file__).parent.parent / "data" / "research_exports"
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def export_txt(self, task: ResearchTask, chapters: list[ScrapedChapter]) -> str:
        """Export as plain text file.
        
        Returns:
            File path to exported TXT
        """
        filename = f"{task.id}_export.txt"
        filepath = self.output_dir / filename
        
        lines = [
            f"=== 调研报告 ===",
            f"来源：{task.source_id}",
            f"URL: {task.target_url}",
            f"章节数：{len(chapters)}",
            f"",
        ]
        
        for i, chapter in enumerate(chapters, 1):
            lines.extend([
                f"\n{'='*60}\n",
                f"第{i}章：{chapter.title or '(无标题)'}",
                f"{'='*60}\n",
                chapter.content,
            ])
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        
        return str(filepath)
    
    def export_epub(self, task: ResearchTask, chapters: list[ScrapedChapter]) -> str:
        """Export as EPUB document.
        
        Returns:
            File path to exported EPUB
        """
        if epub is None:
            raise RuntimeError("ebooklib not installed - cannot export EPUB")
        
        filename = f"{task.id}_export.epub"
        filepath = self.output_dir / filename
        
        # Create book
        book = epub.EpubBook()
        
        # Set metadata
        book.set_identifier(task.id)
        book.set_title(task.source_id, "Research Report")
        book.set_author("NovelForge Research Engine")
        
        # Add table of contents
        spc_chapters = []
        toc = []
        
        for i, chapter in enumerate(chapters, 1):
            # Create chapter content with CSS styling
            soup = BeautifulSoup(chapter.content, "html.parser")
            content_html = str(soup)
            
            chapter_item = epub.EpubHtml(
                title=chapter.title or f"Chapter {i}",
                file_name=f"chap_{i:03d}.xhtml",
                lang="zh-CN",
            )
            
            chapter_item.content = f"""
            <!DOCTYPE html>
            <html xmlns="http://www.w3.org/1999/xhtml">
            <head>
                <title>{chapter.title or 'Chapter'}</title>
                <style>
                    body {{ font-family: Georgia, serif; line-height: 1.8; padding: 2em; }}
                    h1 {{ color: #333; border-bottom: 2px solid #ddd; padding-bottom: 0.5em; }}
                    p {{ margin: 1em 0; text-indent: 2em; }}
                </style>
            </head>
            <body>
                <h1>{chapter.title or f'第{i}章'}</h1>
                {content_html}
            </body>
            </html>
            """
            
            spc_chapters.append(chapter_item)
            toc.append(chapter_item)
        
        # Add NCX and Nav files
        book.toc = toc
        book.add_item(*spc_chapters)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        
        # Define CSS
        css = """
        @page { margin: 10% }
        body { font-family: Georgia, serif; font-size: 14pt; line-height: 1.6; }
        h1 { font-size: 18pt; margin: 1em 0 0.5em; }
        p { margin: 0.5em 0 0.5em 2em; }
        """
        nav_css = epub.EpubItem(uid="style_nav", file_name="style/nav.css", media_type="text/css", content=css)
        book.add_item(nav_css)
        
        # Write EPUB
        epub.write_epub(str(filepath), book, {})
        
        return str(filepath)
    
    def export_pdf(self, task: ResearchTask, chapters: list[ScrapedChapter]) -> str:
        """Export as PDF (requires reportlab).
        
        Returns:
            File path to exported PDF
        """
        try:
            from reportlab.lib.pagesizes import A5
            from reportlab.pdfgen import canvas
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        except ImportError:
            raise RuntimeError("reportlab not installed - cannot export PDF")
        
        filename = f"{task.id}_export.pdf"
        filepath = self.output_dir / filename
        
        doc = SimpleDocTemplate(str(filepath), pagesize=A5)
        styles = getSampleStyleSheet()
        
        # Custom style for Chinese fonts
        custom_style = ParagraphStyle(
            name="CustomParagraph",
            parent=styles["BodyText"],
            fontSize=11,
            leading=16,
            spaceBefore=10,
            spaceAfter=10,
            fontName="Helvetica",  # Simplified fallback
        )
        
        story = []
        
        # Title page
        story.append(Paragraph(f"Research Report: {task.source_id}", styles["Heading1"]))
        story.append(Spacer(1, 20))
        story.append(Paragraph(f"Source URL: {task.target_url}", styles["Normal"]))
        story.append(Spacer(1, 20))
        story.append(Paragraph(f"Total Chapters: {len(chapters)}", styles["Normal"]))
        story.append(Spacer(1, 50))
        
        # Content chapters
        for i, chapter in enumerate(chapters, 1):
            story.append(Spacer(1, 20))
            story.append(Paragraph(f"Chapter {i}: {chapter.title or '(No Title)'}", styles["Heading2"]))
            
            # Split content into paragraphs
            paragraphs = chapter.content.split("\n\n")
            for para in paragraphs[:20]:  # Limit per chapter to avoid huge PDFs
                if para.strip():
                    story.append(Paragraph(para.strip(), custom_style))
                    story.append(Spacer(1, 5))
        
        doc.build(story)
        
        return str(filepath)
    
    def export_all(self, task: ResearchTask, chapters: list[ScrapedChapter]) -> dict[str, str]:
        """Export in all available formats.
        
        Returns:
            Map of format → file path
        """
        results = {}
        
        # TXT always available
        try:
            txt_path = self.export_txt(task, chapters)
            results["txt"] = txt_path
        except Exception as e:
            print(f"Error exporting TXT: {e}")
        
        # EPUB
        try:
            epub_path = self.export_epub(task, chapters)
            results["epub"] = epub_path
        except Exception as e:
            print(f"Error exporting EPUB: {e}")
        
        # PDF
        try:
            pdf_path = self.export_pdf(task, chapters)
            results["pdf"] = pdf_path
        except Exception as e:
            print(f"Error exporting PDF: {e}")
        
        return results


def batch_export_task_results(task: ResearchTask, db_path: Path) -> dict[str, str]:
    """Batch export for a completed research task.
    
    This function queries the SQLite database for all chapters associated with
    a task ID and exports them in all available formats.
    """
    import sqlite3
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT id, title, url, word_count FROM scraped_chapters WHERE task_id=?",
        (task.id,)
    )
    rows = cursor.fetchall()
    
    chapters = [
        ScrapedChapter(
            id=row[0],
            title=row[1] or "",
            content="",  # Content would be in separate storage
            url=row[2],
            order=i,
            word_count=row[3] or 0,
        )
        for i, row in enumerate(rows, 1)
    ]
    
    conn.close()
    
    exporter = ResearchExporter()
    return exporter.export_all(task, chapters)
