"""Data models for nanobot structured memory workflow."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MemoryCategory(str, Enum):
    """Structured memory categories."""

    PROFILE = "profile"
    PREFERENCES = "preferences"
    ENTITIES = "entities"
    EVENTS = "events"
    CASES = "cases"
    PATTERNS = "patterns"


class DedupDecision(str, Enum):
    """Candidate memory deduplication decisions."""

    CREATE = "create"
    MERGE = "merge"
    SKIP = "skip"


@dataclass(slots=True)
class CandidateMemory:
    """A memory candidate extracted from a session."""

    category: MemoryCategory
    abstract: str
    overview: str
    content: str
    source_session: str
    language: str = "zh-CN"


@dataclass(slots=True)
class DedupResult:
    """Deduplication result for a candidate memory."""

    decision: DedupDecision
    candidate: CandidateMemory
    similar_memories: list[str]
    reason: str = ""


@dataclass(slots=True)
class CompressionResult:
    """Aggregated result stats for session compression."""

    created: int = 0
    merged: int = 0
    skipped: int = 0
    summary: str = ""


CATEGORY_DIRS: dict[MemoryCategory, str] = {
    MemoryCategory.PROFILE: "memories/profile.md",
    MemoryCategory.PREFERENCES: "memories/preferences",
    MemoryCategory.ENTITIES: "memories/entities",
    MemoryCategory.EVENTS: "memories/events",
    MemoryCategory.CASES: "memories/cases",
    MemoryCategory.PATTERNS: "memories/patterns",
}

