"""Script to import/convert so-novel rule files to NovelForge format.

This script will:
1. Download sample rule files from so-novel repository (if available)
2. Parse their YAML/JSON structure
3. Convert to NovelForge's ResearchSourceRule model
4. Save to backend/data/research_sources.json

Usage:
    python -m scripts.import_sonovel_rules [--dry-run]
"""

import json
import sys
from pathlib import Path
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models.research_source import OutputFormat, ResearchSourceRule


def parse_yaml_safe(content: str) -> dict[str, Any]:
    """Parse YAML content safely without external dependencies."""
    try:
        import yaml
        return yaml.safe_load(content)
    except ImportError:
        print("Warning: pyyaml not installed, using fallback parser")
        # Simple fallback: treat as JSON if valid
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            print(f"Failed to parse:\n{content[:200]}...")
            return {}


def load_sample_rules(rules_dir: Path) -> list[dict[str, Any]]:
    """Load and merge all rule files from rules/ directory."""
    all_rules = []
    
    if not rules_dir.exists():
        print(f"Rules directory not found: {rules_dir}")
        print("Creating a default sample rule set instead...")
        return [create_default_sample_rule()]
    
    # Scan for YAML/JSON files
    for file_path in rules_dir.rglob("*"):
        if file_path.suffix in [".yaml", ".yml", ".json"]:
            print(f"Loading: {file_path.relative_to(rules_dir)}")
            try:
                content = file_path.read_text(encoding="utf-8")
                
                # Try YAML first, fall back to JSON
                if file_path.suffix in [".yaml", ".yml"]:
                    data = parse_yaml_safe(content)
                else:
                    data = json.loads(content)
                
                if isinstance(data, dict):
                    all_rules.append(data)
                elif isinstance(data, list):
                    all_rules.extend(data)
                    
            except Exception as e:
                print(f"Error loading {file_path}: {e}")
    
    return all_rules


def create_default_sample_rule() -> dict[str, Any]:
    """Create a sample rule for testing when no so-novel rules are available."""
    return {
        "name": "起点中文网 (示例)",
        "base_url": "https://www.qidian.com",
        "chapter_list_selector": "ul.send-list li a",
        "title_selector": "h1.title",
        "content_selector": "div.chapter-content",
        "pagination_selector": "a.next-page",
        "output_format": "txt",
        "encoding": "utf-8",
        "rate_limit": 1.0,
        "description": "Mainstream Chinese web novel platform (sample rule)",
        "tags": ["novel", "fiction", "chinese"]
    }


def convert_to_novelforge_format(raw_rules: list[dict[str, Any]]) -> list[ResearchSourceRule]:
    """Convert raw rule dicts to NovelForge ResearchSourceRule models."""
    converted = []
    
    for i, raw in enumerate(raw_rules):
        try:
            # Map common field names to our schema
            name = raw.get("name") or raw.get("source_name") or f"Source_{i}"
            base_url = raw.get("base_url") or raw.get("url") or ""
            
            # Determine selectors (handle various naming conventions)
            chapter_list_selector = (
                raw.get("chapter_list_selector") 
                or raw.get("list_selector") 
                or raw.get("pages_selector")
                or "a[href]"
            )
            
            title_selector = (
                raw.get("title_selector")
                or raw.get("chapter_title_selector")
                or "h1"
            )
            
            content_selector = (
                raw.get("content_selector")
                or raw.get("text_selector")
                or raw.get("body_selector")
                or "div.content"
            )
            
            pagination_selector = (
                raw.get("pagination_selector")
                or raw.get("next_selector")
                or None
            )
            
            # Format & encoding
            output_format_str = raw.get("output_format", "txt").lower()
            try:
                output_format = OutputFormat(output_format_str)
            except ValueError:
                output_format = OutputFormat.TXT
            
            rate_limit = float(raw.get("rate_limit") or raw.get("throttle") or 0.5)
            encoding = raw.get("encoding", "utf-8")
            
            # Create model instance
            rule = ResearchSourceRule(
                name=name,
                base_url=base_url,
                chapter_list_selector=chapter_list_selector,
                chapter_detail_selector=raw.get("detail_selector"),
                title_selector=title_selector,
                content_selector=content_selector,
                pagination_selector=pagination_selector,
                output_format=output_format,
                rate_limit=max(0, min(rate_limit, 10)),
                encoding=encoding,
                description=raw.get("description") or raw.get("desc"),
                tags=raw.get("tags") or raw.get("categories", []),
            )
            
            converted.append(rule)
            print(f"✓ Converted: {name}")
            
        except Exception as e:
            print(f"✗ Failed to convert rule {i}: {e}")
            print(f"   Raw data: {str(raw)[:200]}...")
    
    return converted


def save_rules_json(rules: list[ResearchSourceRule], output_path: Path) -> None:
    """Save rules list to JSON file with examples."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Convert to JSON-serializable dicts
    rules_data = [rule.model_dump() for rule in rules]
    
    # Add metadata
    metadata = {
        "version": "1.0",
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "source": "so-novel rules importer",
        "total_sources": len(rules),
    }
    
    # Write with pretty formatting
    output_path.write_text(
        json.dumps({**metadata, "sources": rules_data}, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    
    print(f"\nSaved {len(rules)} rules to {output_path}")


def main(dry_run: bool = False) -> None:
    """Main entry point."""
    # Determine paths
    base_dir = Path(__file__).parent.parent
    rules_sample_dir = base_dir / "rules_sample"  # so-novel rules downloaded earlier
    output_path = base_dir / "backend" / "data" / "research_sources.json"
    
    print("=" * 60)
    print("So Novel Rules Importer → NovelForge Format")
    print("=" * 60)
    
    # Load raw rules
    print(f"\nScanning rules directory: {rules_sample_dir}")
    raw_rules = load_sample_rules(rules_sample_dir)
    
    if not raw_rules:
        print("\nNo rules found, creating sample default rule...")
        raw_rules = [create_default_sample_rule()]
    
    # Convert to NovelForge format
    print(f"\nConverting {len(raw_rules)} rules to NovelForge schema...")
    converted = convert_to_novelforge_format(raw_rules)
    
    if dry_run:
        print("\n=== DRY RUN ===")
        print(json.dumps([r.model_dump() for r in converted], indent=2, ensure_ascii=False))
        print("=== END DRY RUN ===\n")
        return
    
    # Save output
    save_rules_json(converted, output_path)
    
    print("\n✓ Import complete!")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Import so-novel rules to NovelForge")
    parser.add_argument("--dry-run", action="store_true", help="Print results without saving")
    args = parser.parse_args()
    
    main(dry_run=args.dry_run)
