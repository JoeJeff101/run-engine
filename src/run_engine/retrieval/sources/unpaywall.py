"""Unpaywall -- legal open-access copies for a known DOI.

Not a search engine. It answers exactly one question: "given this DOI, is there
a legally free full text somewhere?" So it is an enrichment step rather than a
chain member -- run it over records you already have.

Requires a contact email in every request; that is Unpaywall's stated condition
of use, not an optional courtesy. Without CONTACT_EMAIL set, this skips.

API: https://api.unpaywall.org/v2
"""

from __future__ import annotations

from typing import Iterable

from ...evidence.models import Record
from .base import contact_email, http_get

BASE = "https://api.unpaywall.org/v2"
PROVIDER = "unpaywall"


def best_oa_url(doi: str) -> str | None:
    """The best legal free copy for a DOI, or None."""
    email = contact_email()
    if not email or not doi:
        return None
    payload = http_get(PROVIDER, f"{BASE}/{doi.strip()}", params={"email": email})
    if not payload:
        return None
    location = payload.get("best_oa_location") or {}
    return location.get("url_for_pdf") or location.get("url")


def enrich(records: Iterable[Record], limit: int = 10) -> list[Record]:
    """Fill in open-access links for records that have a DOI but no PDF.

    Bounded by ``limit`` on purpose: this is one HTTP round trip per record, and
    running it across a broad sweep would cost more time than the links are
    worth. Enrich the few records you actually intend to read.
    """
    out: list[Record] = []
    spent = 0
    for rec in records:
        if rec.doi and not rec.open_access_pdf and spent < limit:
            spent += 1
            try:
                url = best_oa_url(rec.doi)
            except Exception:
                # Enrichment is strictly optional; never let it fail a search.
                url = None
            if url:
                rec.open_access_pdf = url
        out.append(rec)
    return out
