"""Retrieval: intent routing, the source chain, and query reformulation.

Usable with no agents at all. The router answers a question from sources and
returns a ResearchResult; everything above it in the engine is optional.
"""

from __future__ import annotations

from .profiles import BUDGETS, INTENTS, resolve_intents
from .query import keywordize, normalize_title
from .router import Router, SourceUnavailable

__all__ = [
    "BUDGETS", "INTENTS", "Router", "SourceUnavailable",
    "keywordize", "normalize_title", "resolve_intents",
]
