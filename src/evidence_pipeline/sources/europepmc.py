"""Europe PMC -- life-sciences literature, including preprints.

The preprint path is the reason this connector is worth having separately from
PubMed. Europe PMC maintains an official, keyword-searchable preprint index, so
bioRxiv and medRxiv become reachable through the same query interface as the
journal literature -- no scraping, no separate integration.

That matters for prior-art work specifically. In a fast-moving field the thing
that pre-empts an idea is frequently a preprint from four months ago that no
journal index has picked up yet. A search that only covers published literature
will confidently tell you the ground is clear.

API: https://www.ebi.ac.uk/europepmc/webservices/rest  |  Rate: no key required.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..models import Record
from ..query import excerpt_around, keywordize
from .base import clean, http_get, make_record

BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
PROVIDER = "europepmc"

# Europe PMC's source code for preprint servers.
PREPRINT_FILTER = "SRC:PPR"


def _to_record(item: dict[str, Any], terms: str, excerpt_chars: int, preprint: bool) -> Record | None:
    abstract = item.get("abstractText")
    source_code = (item.get("source") or "").upper()

    # Relabel so the provenance says "bioRxiv", not "europepmc", when that is
    # what it actually is. Where a record came from is part of how much weight
    # it deserves, and flattening that to the aggregator loses real information.
    publisher = PROVIDER
    if preprint or source_code == "PPR":
        raw = (item.get("bookOrReportDetails") or {}).get("publisher") or ""
        low = f"{raw} {item.get('journalTitle') or ''}".lower()
        if "medrxiv" in low:
            publisher = "medrxiv"
        elif "biorxiv" in low:
            publisher = "biorxiv"
        else:
            publisher = "preprint"

    return make_record(
        publisher,
        item.get("title"),
        doi=item.get("doi"),
        pmid=item.get("pmid"),
        entity_id=item.get("id"),
        url=f"https://europepmc.org/article/{item.get('source')}/{item.get('id')}"
        if item.get("source") and item.get("id")
        else None,
        year=int(item["pubYear"]) if str(item.get("pubYear") or "").isdigit() else None,
        venue=clean(item.get("journalTitle"), 200),
        authors=[a.strip() for a in (item.get("authorString") or "").split(",") if a.strip()][:12],
        abstract=clean(abstract),
        excerpt=excerpt_around(abstract or "", terms, excerpt_chars) or None,
        citation_count=item.get("citedByCount"),
        authority="indexed",
        extra={"is_preprint": preprint or source_code == "PPR"},
    )


def _query(terms: str, per_source: int, preprints: bool) -> list[dict[str, Any]]:
    q = f"({terms}) AND ({PREPRINT_FILTER})" if preprints else terms
    payload = http_get(
        PROVIDER,
        BASE,
        params={
            "query": q,
            "format": "json",
            "pageSize": max(1, min(per_source, 100)),
            "resultType": "core",  # includes abstracts
        },
    )
    if not payload:
        return []
    return (payload.get("resultList") or {}).get("result") or []


def search(
    query: str,
    per_source: int = 10,
    want_fulltext: bool = False,
    excerpt_chars: int = 600,
    include_preprints: bool = True,
    **_: Any,
) -> Iterable[Record]:
    terms = keywordize(query)
    records: list[Record] = []

    for item in _query(terms, per_source, preprints=False):
        rec = _to_record(item, terms, excerpt_chars, preprint=False)
        if rec:
            records.append(rec)

    if include_preprints:
        # Deliberately a second call rather than an OR: preprints are worth a
        # guaranteed slice of the budget, and folding them into one query lets
        # the published literature crowd them out entirely.
        budget = max(2, per_source // 3)
        for item in _query(terms, budget, preprints=True):
            rec = _to_record(item, terms, excerpt_chars, preprint=True)
            if rec:
                records.append(rec)

    return records
