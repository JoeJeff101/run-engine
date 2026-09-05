"""Crossref -- the DOI registry.

Used less for discovery than as *glue*. When two databases disagree about
whether a DOI is real, or a record arrives with a title and no identifier,
Crossref is the arbiter.

That role comes with a trap. Crossref's relevance search always returns
something, so a title lookup for a paper it has never seen still yields a
confident-looking best match. Accepting that blindly attaches the wrong DOI to
a record -- and a wrong identifier is far worse than a missing one, because
everything downstream treats identifiers as ground truth. Hence the similarity
gate below: a candidate must clear 0.85 title similarity or it is discarded.

API: https://api.crossref.org  |  Rate: polite pool via mailto.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Iterable

from ...evidence.models import Record
from ..query import keywordize, normalize_title
from .base import clean, contact_email, http_get, make_record

BASE = "https://api.crossref.org/works"
PROVIDER = "crossref"

# Below this, a "best match" is not a match. Tuned against known-absent titles:
# genuine matches with punctuation and subtitle drift land around 0.90+, while
# unrelated best-guesses rarely clear 0.7.
TITLE_MATCH_THRESHOLD = 0.85


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def _params(extra: dict[str, Any]) -> dict[str, Any]:
    params = dict(extra)
    email = contact_email()
    if email:
        params["mailto"] = email
    return params


def _to_record(item: dict[str, Any]) -> Record | None:
    titles = item.get("title") or []
    title = titles[0] if titles else None
    authors = []
    for person in item.get("author") or []:
        name = " ".join(x for x in (person.get("given"), person.get("family")) if x)
        if name:
            authors.append(name)
    issued = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
    year = issued[0] if issued else None
    containers = item.get("container-title") or []

    return make_record(
        PROVIDER,
        title,
        doi=item.get("DOI"),
        url=item.get("URL"),
        year=year if isinstance(year, int) else None,
        venue=clean(containers[0] if containers else None, 200),
        authors=authors[:12],
        abstract=clean(item.get("abstract")),
        citation_count=item.get("is-referenced-by-count"),
        authority="indexed",
    )


def search(
    query: str,
    per_source: int = 10,
    want_fulltext: bool = False,
    excerpt_chars: int = 600,
    **_: Any,
) -> Iterable[Record]:
    payload = http_get(
        PROVIDER,
        BASE,
        params=_params({"query": keywordize(query), "rows": max(1, min(per_source, 100))}),
    )
    if not payload:
        return []
    records: list[Record] = []
    for item in (payload.get("message") or {}).get("items") or []:
        rec = _to_record(item)
        if rec:
            records.append(rec)
    return records


def resolve_doi(title: str) -> str | None:
    """Find the DOI for a known title, or nothing.

    Returns None rather than a best guess. See the module docstring: a confident
    wrong identifier is the failure mode being defended against here.
    """
    if not title or not title.strip():
        return None
    payload = http_get(
        PROVIDER,
        BASE,
        params=_params({"query.bibliographic": title, "rows": 3}),
    )
    if not payload:
        return None
    for item in (payload.get("message") or {}).get("items") or []:
        candidates = item.get("title") or []
        if not candidates:
            continue
        if _title_similarity(title, candidates[0]) >= TITLE_MATCH_THRESHOLD:
            return item.get("DOI")
    return None
