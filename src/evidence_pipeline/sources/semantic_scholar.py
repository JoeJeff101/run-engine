"""Semantic Scholar Academic Graph.

Earns its place for two fields no other free source provides: ``tldr``, a
one-line machine summary, and ``influentialCitationCount``, which separates
citations that actually built on a paper from citations that merely mention it.

The TLDR is disproportionately valuable downstream. A one-line summary costs
roughly a twentieth of an abstract in tokens and answers "is this relevant?"
just as well, which is why ``Record.best_text`` reaches for it first.

Unauthenticated traffic is shaped aggressively -- a 1.1s interval and a documented
back-off on 429. An API key raises the ceiling but is not required.

API: https://api.semanticscholar.org/graph/v1  |  Rate: heavily shaped without a key.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from ..models import Record
from ..query import excerpt_around, keywordize
from .base import clean, http_get, make_record

BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
PROVIDER = "semantic_scholar"

FIELDS = ",".join(
    [
        "title", "year", "venue", "externalIds", "abstract", "tldr",
        "citationCount", "influentialCitationCount", "openAccessPdf", "authors",
    ]
)


def search(
    query: str,
    per_source: int = 10,
    want_fulltext: bool = False,
    excerpt_chars: int = 600,
    **_: Any,
) -> Iterable[Record]:
    terms = keywordize(query)
    headers: dict[str, str] = {}
    key = (os.environ.get("S2_API_KEY") or "").strip()
    if key:
        headers["x-api-key"] = key

    payload = http_get(
        PROVIDER,
        BASE,
        params={"query": terms, "limit": max(1, min(per_source, 100)), "fields": FIELDS},
        headers=headers,
    )
    if not payload:
        return []

    records: list[Record] = []
    for paper in payload.get("data") or []:
        ids = paper.get("externalIds") or {}
        tldr = (paper.get("tldr") or {}).get("text")
        abstract = paper.get("abstract")
        oa = (paper.get("openAccessPdf") or {}).get("url")
        authors = [a.get("name") for a in (paper.get("authors") or []) if a.get("name")]

        rec = make_record(
            PROVIDER,
            paper.get("title"),
            doi=ids.get("DOI"),
            pmid=str(ids["PubMed"]) if ids.get("PubMed") else None,
            entity_id=ids.get("CorpusId") and f"S2:{ids['CorpusId']}",
            year=paper.get("year"),
            venue=clean(paper.get("venue"), 200),
            authors=authors[:12],
            abstract=clean(abstract),
            tldr=clean(tldr, 600),
            excerpt=excerpt_around(abstract or "", terms, excerpt_chars) or None,
            citation_count=paper.get("citationCount"),
            open_access_pdf=oa,
            authority="indexed",
            extra={"influential_citations": paper.get("influentialCitationCount")},
        )
        if rec:
            records.append(rec)
    return records
