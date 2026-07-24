"""Model Gateway Provider Adapter - Canonical Event types and stream parsing.
Per §11 v7.3 spec: reasoning/final whitelist separation, cross-chunk state machine.
"""
from enum import Enum
from pydantic import BaseModel
from typing import Any


# Thinking tags - defined as constants to avoid any encoding issues
THINK_OPEN = chr(60) + "think" + chr(62)      # <think>
THINK_CLOSE = chr(60) + "/think" + chr(62)     # </think>


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
    """Cross-chunk state machine for <think></think> tags.
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
                think_pos = self.carry.find(THINK_OPEN)
                if think_pos == -1:
                    # No open tag found
                    if THINK_OPEN[0] in self.carry or "<" in self.carry:
                        # Might be partial tag - keep 32 chars as carry
                        if len(self.carry) <= 32:
                            break
                        safe_end = len(self.carry) - 32
                        results.append((CanonicalEventType.FINAL, self.carry[:safe_end]))
                        self.carry = self.carry[safe_end:]
                    else:
                        # No angle bracket at all, emit everything
                        results.append((CanonicalEventType.FINAL, self.carry))
                        self.carry = ""
                        break
                elif think_pos < len(self.carry) - len(THINK_OPEN) + 1:
                    # Found open tag with enough room after it
                    if think_pos > 0:
                        results.append((CanonicalEventType.FINAL, self.carry[:think_pos]))
                    self.carry = self.carry[think_pos + len(THINK_OPEN):]
                    self.mode = InlineMode.REASONING
                else:
                    # Open tag too close to end, might be partial - wait for more
                    break

            if self.mode == InlineMode.REASONING:
                end_pos = self.carry.find(THINK_CLOSE)
                if end_pos == -1:
                    # No close tag found yet
                    if len(self.carry) <= 32:
                        break
                    safe_end = len(self.carry) - 32
                    results.append((CanonicalEventType.REASONING, self.carry[:safe_end]))
                    self.carry = self.carry[safe_end:]
                elif end_pos < len(self.carry) - len(THINK_CLOSE) + 1:
                    # Found close tag
                    if end_pos > 0:
                        results.append((CanonicalEventType.REASONING, self.carry[:end_pos]))
                    self.carry = self.carry[end_pos + len(THINK_CLOSE):]
                    self.mode = InlineMode.FINAL
                else:
                    # Close tag too close to end, might be partial - wait
                    break

            # Safety: if carry is empty, break
            if not self.carry:
                break

        return results

    def flush(self) -> list[tuple[CanonicalEventType, str]]:
        """Call at end of stream."""
        results = []
        if self.mode == InlineMode.REASONING:
            # Unterminated reasoning - quarantine
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
    "inline_reasoning_tags": [[THINK_OPEN, THINK_CLOSE], ["<analysis>", "</analysis>"]],
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
    if delta.get("thinking"):
        return CanonicalEventType.REASONING, delta["thinking"]
    if delta.get("thought"):
        return CanonicalEventType.REASONING, delta["thought"]
    if delta.get("content"):
        return CanonicalEventType.FINAL, delta["content"]
    if delta.get("tool_calls"):
        return CanonicalEventType.TOOL, ""
    return CanonicalEventType.UNKNOWN, ""
