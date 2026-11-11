"""Layered, transparent, user-controlled persistent memory.

``LayeredMemory`` is the store; ``MemoryService`` is the part the application
talks to (background extraction, retrieval, settings, audit). Importing the
store alone is still enough for tests and for tools that only need to read.
"""

from .extract import Candidate, ModelExtractor, RuleExtractor, filter_candidates
from .service import AUTO_CATEGORIES, MemoryService
from .store import CATEGORIES, LayeredMemory

__all__ = [
    "LayeredMemory", "CATEGORIES", "MemoryService", "AUTO_CATEGORIES",
    "Candidate", "RuleExtractor", "ModelExtractor", "filter_candidates",
]
