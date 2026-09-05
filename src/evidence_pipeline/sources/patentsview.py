"""USPTO PatentsView -- granted US patents.

This is the connector the whole pipeline is built around, because it reaches
material no scholarly index can see. Patents carry no DOI, are not indexed by
any of the literature sources here, and are frequently the *only* place an idea
appears before someone spends a year rediscovering it. A prior-art search that
queries only papers is not a prior-art search.

Two operational notes:

The legacy ``api.patentsview.org`` endpoint was retired and now answers 410.
The current service is ``search.patentsview.org/api/v1``, and it requires a key.

Records from here never have a DOI, so ``dedup.cluster_key`` falls through to
the patent number as the entity id. That is correct and intentional -- patent
numbers are stronger identifiers than DOIs, they just are not DOIs.

Requires PATENTSVIEW_API_KEY (free, on request). Without one this connector skips.

API: https://search.patentsview.org/api/v1
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

from ..models import Record
from ..query import excerpt_around, keywordize
from .base import clean, http_get, make_record

BASE = "https://search.patentsview.org/api/v1/patent/"
PROVIDER = "patentsview"


def search(
    query: str,
    per_source: int = 10,
    want_fulltext: bool = False,
    excerpt_chars: int = 600,
    **_: Any,
) -> Iterable[Record]:
    key = (os.environ.get("PATENTSVIEW_API_KEY") or "").strip()
    if not key:
        return []

    terms = keywordize(query)

    # _text_any over title OR abstract. Deliberately ANY rather than ALL:
    # patent drafters use language chosen to be broad, so the vocabulary rarely
    # matches a researcher's phrasing term for term. An ALL query against
    # patents reliably returns nothing and reads as "the field is clear".
    criteria = {
        "_or": [
            {"_text_any": {"patent_title": terms}},
            {"_text_any": {"patent_abstract": terms}},
        ]
    }
    fields = [
        "patent_id", "patent_title", "patent_abstract", "patent_date",
        "patent_type", "assignees.assignee_organization",
    ]

    payload = http_get(
        PROVIDER,
        BASE,
        params={
            "q": json.dumps(criteria),
            "f": json.dumps(fields),
            "o": json.dumps({"size": max(1, min(per_source, 100))}),
        },
        headers={"X-Api-Key": key},
    )
    if not payload:
        return []

    records: list[Record] = []
    for patent in payload.get("patents") or []:
        abstract = patent.get("patent_abstract")
        date = str(patent.get("patent_date") or "")
        year = int(date[:4]) if date[:4].isdigit() else None
        assignees = [
            a.get("assignee_organization")
            for a in (patent.get("assignees") or [])
            if a.get("assignee_organization")
        ]
        pid = patent.get("patent_id")

        rec = make_record(
            PROVIDER,
            patent.get("patent_title"),
            entity_id=f"USPTO:{pid}" if pid else None,
            url=f"https://patents.google.com/patent/US{pid}" if pid else None,
            year=year,
            venue="US patent",
            authors=assignees[:12],
            abstract=clean(abstract),
            excerpt=excerpt_around(abstract or "", terms, excerpt_chars) or None,
            authority="primary_db",
            extra={"patent_type": patent.get("patent_type"), "granted": date or None},
        )
        if rec:
            records.append(rec)
    return records
