"""Typed evidence: records, deduplication, and the ledger that grades them.

Separated from retrieval on purpose. Retrieval decides *what was found*; this
package decides *what may be believed about it*, and the second question has a
different failure mode from the first -- a bad search returns too little, a bad
ledger returns a fabrication wearing a citation.
"""

from __future__ import annotations

from .ledger import GRADES, PROMOTABLE, Row, StagingLedger, grade_for, promote, row_from_record
from .models import Record, ResearchResult

__all__ = [
    "GRADES", "PROMOTABLE", "Record", "ResearchResult", "Row", "StagingLedger",
    "grade_for", "promote", "row_from_record",
]
