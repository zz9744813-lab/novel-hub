"""Model Gateway Provider Adapter - Canonical Event types and stream parsing.
Per §11 v7.3 spec: reasoning/final whitelist separation, cross-chunk <think> state machine.
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


class InlineReasoningParser:
    """Cross-chunk  state machine.
    Per §11.4: preserve 32-char carry buffer, detect  across chunks.
    """
    def __init__(self):
        self.mode = InlineMode.FINAL
        self.carry = ""

    def feed(self, chunk: str) -> list[tuple[CanonicalEventType, str]]:
        """Process a chunk, return list of (event_type, text) pairs."""
        results = []
        self.carry += chunk

        while len(self.carry) > 32 or (len(self.carry) <= 32 and "<" not in self.carry and self.mode == InlineMode.FINAL):
            if self.mode == InlineMode.FINAL:
                think_pos = self.carry.find("<think>")
                if think_pos == -1:
                    if "<" in self.carry:
                        safe_end = max(0, len(self.carry) - 32)
                        text = self.carry[:safe_end]
                        self.carry = self.carry[safe_end:]
                        results.append((CanonicalEventType.FINAL, text))
                        break
                    else:
                        text = self.carry
                        self.carry = ""
                        results.append((CanonicalEventType.FINAL, text))
                        break
                elif think_pos < len(self.carry) - 7:
                    if think_pos > 0:
                        results.append((CanonicalEventType.FINAL, self.carry[:think_pos]))
                    self.carry = self.carry[think_pos + 7:]
                    self.mode = InlineMode.REASONING
                else:
                    break

            if self.mode == InlineMode.REASONING:
                end_pos = self.carry.find("")
                if end_pos == -1:
                    safe_end = max(0, len(self.carry) - 32)
                    text = self.carry[:safe_end]
                    self.carry = self.carry[safe_end:]
                    results.append((CanonicalEventType.REASONING, text))
                    break
                elif end_pos < len(self.carry) - 8:
                    if end_pos > 0:
                        results.append((CanonicalEventType.REASONING, self.carry[:end_pos]))
                    self.carry = self.carry[end_pos + 8:]
                    self.mode = InlineMode.FINAL
                else:
                    break

        return results

    def flush(self) -> list[tuple[CanonicalEventType, str]]:
        """Call at end of stream."""
        results = []
        if self.mode == InlineMode.REASONING:
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
    "reasoning_paths": ["choices[].delta.reasoning_content", "choices[].message.reasoning_content"],
    "tool_paths": ["choices[].delta.tool_calls"],
    "inline_reasoning_tags": [[""], ["<analysis>", "</analysis>"]],
    "unknown_field_policy": "quarantine",
}


def classify_delta(delta: dict) -> tuple[CanonicalEventType, str]:
    """Classify a streaming delta into a canonical event.
    Per §11.2: only FINAL goes into prose buffer. reasoning, tool, unknown = quarantine.
    """
    if delta.get("reasoning_content"):
        return CanonicalEventType.REASONING, delta["reasoning_content"]
    if delta.get("reasoning"):
        return CanonicalEventType.REASONING, delta["reasoning"]
    if delta.get("content"):
        return CanonicalEventType.FINAL, delta["content"]
    if delta.get("tool_calls"):
        return CanonicalEventType.TOOL, ""
    return CanonicalEventType.UNKNOWN, ""
