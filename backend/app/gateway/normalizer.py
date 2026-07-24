"""Normalizer - §11.7: processes final_content only, fixed order."""
import re
import json


def normalize_prose(final_content: str) -> str:
    """Normalize prose output: Unicode, strip markdown fences, detect boundaries."""
    text = final_content.strip()
    # Remove markdown code fences if present
    text = re.sub(r'^```\w*\n', '', text)
    text = re.sub(r'\n```$', '', text)
    # Normalize line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    return text.strip()


def normalize_json(final_content: str) -> dict | None:
    """Normalize JSON output: strip markdown, parse, validate."""
    text = final_content.strip()
    # Remove markdown code fences
    text = re.sub(r'^```\w*\n', '', text)
    text = re.sub(r'\n```$', '', text)
    # Try to find JSON boundaries
    start = text.find('{')
    if start == -1:
        start = text.find('[')
    if start == -1:
        return None
    end = text.rfind('}')
    if end == -1:
        end = text.rfind(']')
    if end == -1:
        return None
    json_str = text[start:end+1]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None


def check_truncation(final_content: str) -> bool:
    """Check if output appears truncated."""
    if not final_content:
        return False
    if len(final_content) < 10:
        return True
    text = final_content.rstrip()
    end_chars = set('。！？!?...”」』\n 》》」』')
    if text and text[-1] not in end_chars:
        return True
    return False


def check_empty(final_content: str) -> bool:
    return not final_content or not final_content.strip()
