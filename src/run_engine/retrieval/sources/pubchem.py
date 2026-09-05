"""PubChem -- a worked example of identifier resolution.

Every other connector in this package searches *documents*. This one searches
*entities*, and it is included because that difference is the hinge the whole
evidence model turns on.

A document says a thing is true. An entity registry says *which thing* you are
talking about, with a stable identifier anyone else can look up. That is the
difference between "a paper reported this value" and "this is the substance,
here is its registry number, and here is the value attached to it". Only the
second kind of claim can be independently checked, which is why ``ledger.py``
grades a record with a resolved registry identifier as REAL and a record backed
only by a citation as EST.

The pattern generalizes. Any domain with an authoritative registry -- gene
databases, materials databases, company registries, standards bodies -- can be
wired in behind this same interface. PubChem is simply the largest one that is
free and keyless.

Note the query shape: a registry indexes *names*, not questions, so a question
must first be decomposed into candidate entity names. See ``query.variants``,
and note the hard cap on how many candidates get tried.

API: https://pubchem.ncbi.nlm.nih.gov/rest/pug  |  Rate: no more than 5 req/s.
"""

from __future__ import annotations

from typing import Any, Iterable

from ...evidence.models import Record
from ..query import keywordize, variants
from .base import http_get, make_record

BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
PROVIDER = "pubchem"

PROPERTIES = "MolecularFormula,MolecularWeight,IUPACName,CanonicalSMILES,InChIKey"


def _cids_for(name: str, limit: int) -> list[int]:
    payload = http_get(PROVIDER, f"{BASE}/compound/name/{name}/cids/JSON")
    if not payload:
        # 404 -- the registry answered, and the answer is "no such entity".
        # Distinct from unreachable, which raises. See sources/base.py.
        return []
    return ((payload.get("IdentifierList") or {}).get("CID") or [])[:limit]


def search(
    query: str,
    per_source: int = 5,
    want_fulltext: bool = False,
    excerpt_chars: int = 600,
    **_: Any,
) -> Iterable[Record]:
    terms = keywordize(query)
    seen: set[int] = set()
    records: list[Record] = []

    for candidate in variants(terms):
        if len(records) >= per_source:
            break
        cids = _cids_for(candidate, limit=max(1, per_source - len(records)))
        fresh = [c for c in cids if c not in seen]
        if not fresh:
            continue
        seen.update(fresh)

        joined = ",".join(str(c) for c in fresh)
        payload = http_get(PROVIDER, f"{BASE}/compound/cid/{joined}/property/{PROPERTIES}/JSON")
        if not payload:
            continue

        for prop in (payload.get("PropertyTable") or {}).get("Properties") or []:
            cid = prop.get("CID")
            name = prop.get("IUPACName") or f"PubChem CID {cid}"
            rec = make_record(
                PROVIDER,
                name,
                entity_id=f"PUBCHEM:{cid}" if cid else None,
                url=f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}" if cid else None,
                venue="entity registry",
                # A registry record is authoritative about identity by
                # definition -- that is what a registry is.
                authority="primary_db",
                extra={
                    "matched_on": candidate,
                    "formula": prop.get("MolecularFormula"),
                    "molecular_weight": prop.get("MolecularWeight"),
                    "smiles": prop.get("CanonicalSMILES"),
                    "inchikey": prop.get("InChIKey"),
                },
            )
            if rec:
                records.append(rec)

    return records[:per_source]
