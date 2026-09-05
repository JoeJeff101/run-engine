"""PubMed via NCBI E-utilities.

Two hops by design: ``esearch`` returns PMIDs, ``esummary`` turns them into
records. Both count against the same rate limit, which is why the throttle key
is the shared ``ncbi`` bucket rather than one per endpoint -- otherwise a single
search would quietly issue traffic at twice the documented rate.

E-utilities allows 3 requests/second without an API key and 10 with one.

API: https://eutils.ncbi.nlm.nih.gov/entrez/eutils
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from ..models import Record
from ..query import keywordize
from .base import clean, contact_email, http_get, make_record

ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
PROVIDER = "pubmed"
THROTTLE_KEY = "ncbi"  # shared across esearch/esummary -- see module docstring


def _common() -> dict[str, Any]:
    params: dict[str, Any] = {"db": "pubmed", "retmode": "json", "tool": "evidence-pipeline"}
    key = (os.environ.get("NCBI_API_KEY") or "").strip()
    if key:
        params["api_key"] = key
    email = contact_email()
    if email:
        params["email"] = email
    return params


def search(
    query: str,
    per_source: int = 10,
    want_fulltext: bool = False,
    excerpt_chars: int = 600,
    **_: Any,
) -> Iterable[Record]:
    terms = keywordize(query)

    found = http_get(
        THROTTLE_KEY,
        ESEARCH,
        params={**_common(), "term": terms, "retmax": max(1, min(per_source, 100))},
    )
    if not found:
        return []
    ids = ((found.get("esearchresult") or {}).get("idlist")) or []
    if not ids:
        return []

    summary = http_get(THROTTLE_KEY, ESUMMARY, params={**_common(), "id": ",".join(ids)})
    if not summary:
        return []
    result = summary.get("result") or {}

    records: list[Record] = []
    for pmid in result.get("uids") or []:
        item = result.get(pmid) or {}
        doi = None
        for ident in item.get("articleids") or []:
            if (ident.get("idtype") or "").lower() == "doi":
                doi = ident.get("value")
                break
        year = None
        pubdate = str(item.get("pubdate") or "")
        if pubdate[:4].isdigit():
            year = int(pubdate[:4])

        rec = make_record(
            PROVIDER,
            item.get("title"),
            doi=doi,
            pmid=str(pmid),
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            year=year,
            venue=clean(item.get("fulljournalname") or item.get("source"), 200),
            authors=[a.get("name") for a in (item.get("authors") or []) if a.get("name")][:12],
            authority="indexed",
        )
        # esummary carries no abstract; that is what europepmc is for. Records
        # from here are identifier-grade, not content-grade, and the merge in
        # dedup.py will back-fill text from a richer copy if one exists.
        if rec:
            records.append(rec)
    return records
