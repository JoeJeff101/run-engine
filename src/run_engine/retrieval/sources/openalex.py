"""OpenAlex -- open catalogue of scholarly works.

The default primary for discovery: broadest coverage of the keyless sources,
no registration, and a documented polite pool. If you only ever wire up one
database, wire up this one.

Abstracts arrive as an *inverted index* -- a map of word to positions rather
than a string -- so reconstruction happens here at the connector boundary. That
is the whole reason connectors exist: nothing downstream should ever have to
know that this particular API returns its abstracts inside out.

API: https://api.openalex.org  |  Rate: polite pool, ~10 req/s with a mailto.
"""

from __future__ import annotations

from typing import Any, Iterable

from ...evidence.models import Record
from ..query import excerpt_around, keywordize
from .base import clean, contact_email, http_get, make_record

BASE = "https://api.openalex.org/works"
PROVIDER = "openalex"


def _reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str | None:
    """Rebuild plain text from an inverted index {word: [positions]}."""
    if not inverted:
        return None
    positions: list[tuple[int, str]] = []
    for word, spots in inverted.items():
        for spot in spots or []:
            positions.append((spot, word))
    if not positions:
        return None
    positions.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in positions)


def _authors(work: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for entry in work.get("authorships") or []:
        name = (entry.get("author") or {}).get("display_name")
        if name:
            out.append(name)
    return out[:12]


def search(
    query: str,
    per_source: int = 10,
    want_fulltext: bool = False,
    excerpt_chars: int = 600,
    **_: Any,
) -> Iterable[Record]:
    terms = keywordize(query)
    params: dict[str, Any] = {
        "search": terms,
        "per-page": max(1, min(per_source, 50)),
        # Ask only for the fields actually used. Payload size is the dominant
        # cost of a broad sweep, and OpenAlex records are large by default.
        #
        # Note `primary_location`, not `host_venue`. The latter was removed from
        # the API and now returns HTTP 400 rather than being ignored -- so a
        # stale field name here takes the whole connector down silently, and the
        # chain quietly falls through to a fallback source. Worth knowing: an
        # invalid `select` is a hard error, not a warning.
        "select": "id,doi,title,publication_year,primary_location,authorships,"
        "abstract_inverted_index,cited_by_count,open_access",
    }
    email = contact_email()
    if email:
        params["mailto"] = email

    payload = http_get(PROVIDER, BASE, params=params)
    if not payload:
        return []

    records: list[Record] = []
    for work in payload.get("results") or []:
        abstract = _reconstruct_abstract(work.get("abstract_inverted_index"))
        oa = work.get("open_access") or {}
        venue = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
        doi = work.get("doi")
        if doi and doi.startswith("https://doi.org/"):
            doi = doi[len("https://doi.org/") :]

        rec = make_record(
            PROVIDER,
            work.get("title"),
            doi=doi,
            entity_id=work.get("id"),
            url=work.get("id"),
            year=work.get("publication_year"),
            venue=clean(venue, 200),
            authors=_authors(work),
            abstract=clean(abstract),
            excerpt=excerpt_around(abstract or "", terms, excerpt_chars) or None,
            citation_count=work.get("cited_by_count"),
            open_access_pdf=oa.get("oa_url"),
            authority="indexed",
        )
        if rec:
            records.append(rec)
    return records
