"""evidence-pipeline: automating the front end of technical research.

Public surface:

    from run_engine import Router, Record, resolve_intents

Everything else is importable but considered internal.
"""

from __future__ import annotations

from .evidence.models import Record, ResearchResult
from .retrieval.profiles import BUDGETS, INTENTS, resolve_intents
from .retrieval.router import Router

__version__ = "0.1.0"

__all__ = [
    "Record",
    "ResearchResult",
    "Router",
    "INTENTS",
    "BUDGETS",
    "resolve_intents",
    "__version__",
]
