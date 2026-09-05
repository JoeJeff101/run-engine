"""Deterministic fake sources, shipped as part of the package.

Two consumers: the test suite, and ``demo.py --offline``.

The test suite replaces *every* adapter key with a fake. That total replacement
is the point -- if only some were faked, a fallback path firing unexpectedly
would reach a live API, and a test suite that quietly makes network calls is
both slow and a liability. Swapping the whole registry means no test can reach
the network even when the routing logic under test is wrong.
"""

from __future__ import annotations

import hashlib
from typing import Callable

from .models import Record
from .router import SourceUnavailable

# Placeholder subject matter for offline runs. Chosen to be recognizable as
# synthetic so nobody mistakes a demo transcript for real retrieval.
FIXTURE_TOPICS = [
    "graphene oxide membrane selectivity",
    "solid-state electrolyte interface stability",
    "perovskite thin film degradation pathways",
    "lithium dendrite suppression strategies",
    "thermal runaway propagation modelling",
]


def _stable_int(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:8], 16)


def fake_source(
    key: str,
    count: int = 3,
    authority: str = "indexed",
    shared_doi: str | None = None,
    fail: bool = False,
    empty: bool = False,
    calls: dict[str, int] | None = None,
) -> Callable:
    """Build one deterministic adapter.

    ``shared_doi`` makes two different fakes return the same DOI, which is how
    the dedup and authority-merge tests get a genuine cross-source duplicate
    rather than a contrived one.
    """

    def adapter(query: str, per_source: int = 10, want_fulltext: bool = False,
                excerpt_chars: int = 600, **_: object) -> list[Record]:
        if calls is not None:
            calls[key] = calls.get(key, 0) + 1
        if fail:
            raise SourceUnavailable(f"{key}: simulated failure")
        if empty:
            return []

        records: list[Record] = []
        for i in range(min(count, per_source)):
            seed = _stable_int(key, query, str(i))
            doi = shared_doi if (shared_doi and i == 0) else f"10.{5000 + (seed % 4000)}/{key}.{i}"
            records.append(
                Record(
                    title=f"{key} result {i} for {query[:40]}",
                    source=key,
                    doi=doi,
                    year=2015 + (seed % 10),
                    venue=f"Journal of {key.title()}",
                    authors=[f"Author {seed % 100}"],
                    abstract=f"Synthetic abstract {seed} discussing {query[:60]}.",
                    citation_count=seed % 250,
                    authority=authority,
                )
            )
        return records

    return adapter


def fake_adapters(calls: dict[str, int] | None = None) -> dict[str, Callable]:
    """A complete registry of fakes covering every real source key."""
    return {
        "openalex": fake_source("openalex", count=4, authority="indexed", calls=calls),
        "semantic_scholar": fake_source("semantic_scholar", count=3, calls=calls),
        "crossref": fake_source("crossref", count=2, calls=calls),
        "europepmc": fake_source("europepmc", count=3, calls=calls),
        "pubmed": fake_source("pubmed", count=2, calls=calls),
        "core": fake_source("core", count=2, authority="oa", calls=calls),
        "patentsview": fake_source("patentsview", count=2, authority="primary_db", calls=calls),
        "pubchem": fake_source("pubchem", count=1, authority="primary_db", calls=calls),
    }
