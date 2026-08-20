"""Unified document parser package."""
from .base import DocumentBlock
from .registry import parse_document

__all__ = ["DocumentBlock", "parse_document"]
