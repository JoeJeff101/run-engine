"""CORE -- aggregated open-access full text.

The only source here that returns whole papers, which makes it the primary for
"how did they actually do it" questions. Methods sections do not survive into
abstracts; if you need to know what someone actually did, you need the text.

Two things this connector does that matter:

**Progressive relaxation.** CORE's search behaves as implicit-AND, so an
eight-term query returns nothing and "nothing" is indistinguishable from "no
such literature". The fix is to retry with successively shorter term lists until
something comes back -- see ``query.relax``.

**Full text is windowed and discarded.** A returned paper can be hundreds of
kilobytes. Keeping it would blow out memory on a broad sweep and blow out a
context window downstream. Only a query-centred excerpt survives past parse
time, and only for the top few records the budget allows.

Requires CORE_API_KEY. Without one this connector skips.

API: https://api.core.ac.uk/v3  |  Rate: ~1 req/s on the free tier.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from ...evidence.models import Record
from ..query import excerpt_around, keywordize, relax
from .base import clean, http_get, make_record

BASE = "https://api.core.ac.uk/v3/search/works"
PROVIDER = "core"


def search(
    query: str,
    per_source: int = 10,
    want_fulltext: bool = True,
    excerpt_chars: int = 600,
    fulltext_for: int = 3,
    **_: Any,
) -> Iterable[Record]:
    key = (os.environ.get("CORE_API_KEY") or "").strip()
    if not key:
        # Not configured is not an error. The chain falls through.
        return []

    terms = keywordize(query)
    headers = {"Authorization": f"Bearer {key}"}

    items: list[dict[str, Any]] = []
    for attempt in relax(terms):
        payload = http_get(
            PROVIDER,
            BASE,
            params={"q": attempt, "limit": max(1, min(per_source, 50))},
            headers=headers,
        )
        items = (payload or {}).get("results") or []
        if items:
            break

    records: list[Record] = []
    for rank, item in enumerate(items):
        # Only the top few records are worth carrying text for.
        keep_text = want_fulltext and rank < max(0, fulltext_for)
        full = item.get("fullText") if keep_text else None
        excerpt = excerpt_around(full or "", terms, excerpt_chars) if full else None
        # `full` goes out of scope here and is never stored on the Record.

        authors = [a.get("name") for a in (item.get("authors") or []) if a.get("name")]
        year = item.get("yearPublished")

        rec = make_record(
            PROVIDER,
            item.get("title"),
            doi=item.get("doi"),
            entity_id=str(item.get("id")) if item.get("id") else None,
            url=item.get("downloadUrl") or item.get("sourceFulltextUrls", [None])[0],
            year=int(year) if isinstance(year, int) or str(year or "").isdigit() else None,
            venue=clean(item.get("publisher"), 200),
            authors=authors[:12],
            abstract=clean(item.get("abstract")),
            excerpt=excerpt or None,
            open_access_pdf=item.get("downloadUrl"),
            authority="oa",
        )
        if rec:
            records.append(rec)
    return records
