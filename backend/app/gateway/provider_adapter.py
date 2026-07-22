"""Model Gateway Provider Adapter - Canonical Event types and stream parsing.
Per §11 v7.3 spec: reasoning/final whitelist separation, cross-chunk state machine.
"""
from enum import Enum
from pydantic import BaseModel
from typing import Any


class CanonicalEventType(str, Enum):
    REASONING = "reasoning"
    FINAL = "final"
    TOOL = "tool"
    USAGE = "usage"
    UNKNOWN = "unknown"
    ERROR = "error"


class CanonicalStreamEvent(BaseModel):
    event_type: CanonicalEventType
    text: str | None = None
    sequence_no: int
    raw_event: dict
    provider: str
    model: str


class InlineMode(str, Enum):
    FINAL = "final"
    REASONING = "reasoning"


# Inline reasoning tag pairs — must not have empty strings
INLINE_TAGS = [
    (""),
    ("<thinking>", "</thinking>"),
    ("<reasoning>", "</reasoning>"),
    ("<analysis>", "</analysis>"),
]


class InlineReasoningParser:
    """Cross-chunk state machine for inline reasoning tags.
    Per §11.4: preserve 32-char carry buffer, detect tags across chunks.
    """
    def __init__(self):
        self.mode = InlineMode.FINAL
        self.carry = ""

    def feed(self, chunk: str) -> list[tuple[CanonicalEventType, str]]:
        """Process a chunk, return list of (event_type, text) pairs."""
        results = []
        self.carry += chunk

        while True:
            if self.mode == InlineMode.FINAL:
                # Search for any opening reasoning tag
                earliest = -1
                matched_tag = None
                for open_tag, close_tag in INLINE_TAGS:
                    pos = self.carry.find(open_tag)
                    if pos != -1 and (earliest == -1 or pos < earliest):
                        earliest = pos
                        matched_tag = (open_tag, close_tag)

                if earliest == -1:
                    # No tag found — emit as final, keeping carry buffer
                    if len(self.carry) > 32:
                        safe_end = len(self.carry) - 32
                        text = self.carry[:safe_end]
                        self.carry = self.carry[safe_end:]
                        if text:
                            results.append((CanonicalEventType.FINAL, text))
                    break
                elif earliest > 0:
                    # Emit text before the tag as final
                    results.append((CanonicalEventType.FINAL, self.carry[:earliest]))
                    self.carry = self.carry[earliest:]
                # Skip the opening tag
                self.carry = self.carry[len(matched_tag[0]):]
                self.mode = InlineMode.REASONING
                self._close_tag = matched_tag[1]
            else:
                # In reasoning mode — look for the closing tag
                close_tag = getattr(self, "_close_tag", "")
                end_pos = self.carry.find(close_tag)
                if end_pos == -1:
                    # Still in reasoning — emit but keep carry buffer
                    if len(self.carry) > 32:
                        safe_end = len(self.carry) - 32
                        text = self.carry[:safe_end]
                        self.carry = self.carry[safe_end:]
                        if text:
                            results.append((CanonicalEventType.REASONING, text))
                    break
                else:
                    # Found closing tag
                    if end_pos > 0:
                        results.append((CanonicalEventType.REASONING, self.carry[:end_pos]))
                    self.carry = self.carry[end_pos + len(close_tag):]
                    self.mode = InlineMode.FINAL
                    self._close_tag = None

        return results

    def flush(self) -> list[tuple[CanonicalEventType, str]]:
        """Call at end of stream."""
        results = []
        if self.mode == InlineMode.REASONING:
            # Unterminated reasoning — quarantine as UNKNOWN
            results.append((CanonicalEventType.UNKNOWN, self.carry))
            self.carry = ""
        elif self.carry:
            results.append((CanonicalEventType.FINAL, self.carry))
            self.carry = ""
        return results

    @property
    def is_unterminated(self) -> bool:
        return self.mode == InlineMode.REASONING


# Provider profile for field mapping
PROVIDER_PROFILE = {
    "provider": "openai-compatible-relay",
    "content_paths": ["choices[].delta.content", "choices[].message.content"],
    "reasoning_paths": ["choices[].delta.reasoning_content", "choices[].delta.reasoning",
                        "choices[].message.reasoning_content"],
    "tool_paths": ["choices[].delta.tool_calls"],
    "inline_reasoning_tags": INLINE_TAGS,
    "unknown_field_policy": "quarantine",
}


def classify_delta(delta: dict) -> tuple[CanonicalEventType, str]:
    """Classify a streaming delta into a canonical event.
    Per §11.2: only FINAL goes into prose buffer. reasoning, tool, unknown = quarantine.
    """
    # Check reasoning fields first
    for field in ("reasoning_content", "reasoning", "thinking", "thought"):
        val = delta.get(field)
        if val:
            return CanonicalEventType.REASONING, val
    # Check content field
    if delta.get("content"):
        return CanonicalEventType.FINAL, delta["content"]
    if delta.get("tool_calls"):
        return CanonicalEventType.TOOL, ""
    return CanonicalEventType.UNKNOWN, ""


def parse_stream_chunk(data: dict, parser: InlineReasoningParser) -> list[tuple[CanonicalEventType, str]]:
    """Parse a stream chunk using the provider adapter.
    
    First classify via delta fields, then run inline parser on content
    to catch any inline reasoning tags.
    """
    results = []
    choices = data.get("choices", [])
    if not choices:
        return results

    delta = choices[0].get("delta", {})

    # Classify the delta fields
    event_type, text = classify_delta(delta)
    
    if event_type == CanonicalEventType.REASONING and text:
        results.append((CanonicalEventType.REASONING, text))
    elif event_type == CanonicalEventType.FINAL and text:
        # Run through inline parser to catch embedded reasoning tags
        results.extend(parser.feed(text))
    elif event_type == CanonicalEventType.TOOL:
        results.append((CanonicalEventType.TOOL, ""))
    elif event_type == CanonicalEventType.UNKNOWN and text:
        results.append((CanonicalEventType.UNKNOWN, text))

    return results
