"""Source connectors.

Nine adapters behind one signature. ``default_adapters()`` is what the router
gets when no registry is passed, and swapping it wholesale is how the test suite
guarantees no test can reach a live API.

On what is deliberately *not* here
----------------------------------
Some databases that would genuinely improve coverage are absent on purpose,
because their terms of use do not permit programmatic querying. Those are
handled as a **human queue**: the pipeline emits the exact search strings, a
person runs them, and the results come back in through the same evidence-staging
path as everything else.

That is a real design position, not an oversight. A pipeline that quietly
scrapes a subscription database produces results its owner cannot publish,
cannot cite in a filing, and cannot defend. The queue is slower and correct.
The same treatment applies to standards bodies and regulatory portals that
publish no API at all.
"""

from __future__ import annotations

from typing import Callable

from . import (
    core_oa,
    crossref,
    europepmc,
    openalex,
    patentsview,
    pubchem,
    pubmed,
    semantic_scholar,
    unpaywall,
)

__all__ = [
    "default_adapters",
    "SOURCE_META",
    "core_oa",
    "crossref",
    "europepmc",
    "openalex",
    "patentsview",
    "pubchem",
    "pubmed",
    "semantic_scholar",
    "unpaywall",
]


def default_adapters() -> dict[str, Callable]:
    """The live registry: source key -> search callable."""
    return {
        "openalex": openalex.search,
        "semantic_scholar": semantic_scholar.search,
        "crossref": crossref.search,
        "europepmc": europepmc.search,
        "pubmed": pubmed.search,
        "core": core_oa.search,
        "patentsview": patentsview.search,
        "pubchem": pubchem.search,
    }


# Documentation surfaced by the demo and by docs/SOURCES.md, kept next to the
# code so it cannot drift from what is actually wired up.
SOURCE_META: dict[str, dict[str, str]] = {
    "openalex": {
        "covers": "scholarly works, broad coverage",
        "auth": "none (mailto recommended)",
        "note": "default primary for discovery",
    },
    "semantic_scholar": {
        "covers": "scholarly works, citation graph",
        "auth": "none (key raises limits)",
        "note": "supplies one-line TLDRs and influential-citation counts",
    },
    "crossref": {
        "covers": "DOI registry",
        "auth": "none (mailto for polite pool)",
        "note": "identifier arbiter; 0.85 title-similarity gate on resolution",
    },
    "europepmc": {
        "covers": "life sciences, incl. preprints",
        "auth": "none",
        "note": "official preprint index; bioRxiv/medRxiv relabelled on return",
    },
    "pubmed": {
        "covers": "biomedical citations",
        "auth": "none (key raises limits)",
        "note": "identifier-grade records; no abstracts via esummary",
    },
    "core": {
        "covers": "open-access full text",
        "auth": "CORE_API_KEY",
        "note": "progressive term relaxation; full text windowed then discarded",
    },
    "patentsview": {
        "covers": "granted US patents",
        "auth": "PATENTSVIEW_API_KEY",
        "note": "unreachable by scholarly indexes; no DOIs",
    },
    "pubchem": {
        "covers": "entity registry",
        "auth": "none",
        "note": "identifier resolution; grades a claim REAL rather than EST",
    },
    "unpaywall": {
        "covers": "open-access links for a known DOI",
        "auth": "CONTACT_EMAIL required",
        "note": "enrichment only, not a chain member",
    },
}
