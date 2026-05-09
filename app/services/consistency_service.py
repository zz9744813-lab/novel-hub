from __future__ import annotations

import json
from pathlib import Path

from app.db import get_conn, get_setting, get_setting_decrypted
from app.services.ai_client import generate_ai_content
from app.services.ai_context import build_context
from app.services.markdown_service import read_markdown, utc_now


async def run_consistency_check(project: str, chapter_path: str):
    """Background task to check consistency for a chapter."""
    try:
        api_key = get_setting_decrypted("ai_api_key")
        base_url = get_setting("ai_base_url")
        model = get_setting("ai_model")
        if not api_key:
            return

        path = Path(chapter_path)
        if not path.exists():
            return
        fm, body = read_markdown(path)

        # Get context
        context = build_context(project, chapter_path, "check")

        prompt = f"""Context:
{context}

Chapter text to check:
{body}

Please list any plot inconsistencies, out-of-character behaviors, or timeline errors you find. Return as a JSON list of strings. If none, return [].
"""
        response = await generate_ai_content(
            api_key,
            base_url,
            model,
            "You are a consistency checker. Return ONLY valid JSON array.",
            prompt,
        )

        if response:
            # Try to parse JSON
            try:
                # Basic cleanup
                clean = response.strip()
                if clean.startswith("```json"):
                    clean = clean[7:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                issues = json.loads(clean.strip())
                if isinstance(issues, list):
                    with get_conn() as conn:
                        conn.execute(
                            """INSERT INTO consistency_reports (chapter_path, created_at, issues)
                               VALUES (?, ?, ?)
                               ON CONFLICT(chapter_path) DO UPDATE SET created_at=excluded.created_at, issues=excluded.issues""",
                            (chapter_path, utc_now().isoformat(), json.dumps(issues)),
                        )
            except Exception as e:
                print(f"Failed to parse consistency JSON: {e}")
    except Exception as e:
        print(f"Consistency check error: {e}")
