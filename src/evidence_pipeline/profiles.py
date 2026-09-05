"""Routing configuration: intents, source chains, and budgets.

Pure configuration. No I/O, no network, no imports beyond the standard library,
so it can be read as documentation and asserted against in tests.

The central idea is that *the question determines the database*. "Who has cited
this?" and "has anyone patented this?" and "what is this compound's registry
identifier?" are three different questions, and answering all three by throwing
keywords at one general index is how you end up confidently missing things.
Each intent names a ranked chain: the database that should be able to answer it,
then the ones worth trying if the first comes back empty.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Intents
# ---------------------------------------------------------------------------
# chain        : ranked source keys; index 0 is the primary, the rest fallbacks
# primary_db   : the source whose records get "primary_db" authority on merge
# want_fulltext: whether it is worth paying for full-text retrieval here
# note         : why this chain is ordered the way it is

INTENTS: dict[str, dict[str, Any]] = {
    "discovery": {
        "chain": ["openalex", "semantic_scholar", "crossref"],
        "primary_db": "openalex",
        "want_fulltext": False,
        "note": (
            "Broad 'what exists' sweep. OpenAlex leads on coverage and is fully "
            "keyless; Semantic Scholar adds a one-line summary per hit; Crossref "
            "backstops when the other two disagree about whether a DOI is real."
        ),
    },
    "fulltext_method": {
        "chain": ["core", "semantic_scholar", "europepmc"],
        "primary_db": "core",
        "want_fulltext": True,
        "note": (
            "'How did they actually do it' questions. Only worth running where "
            "open-access full text exists, because a method never survives into "
            "the abstract."
        ),
    },
    "citation_graph": {
        "chain": ["semantic_scholar", "openalex"],
        "primary_db": "semantic_scholar",
        "want_fulltext": False,
        "note": (
            "Influence and lineage. Both expose citation counts; Semantic Scholar "
            "additionally separates influential citations from incidental ones."
        ),
    },
    "patent_prior_art": {
        "chain": ["patentsview", "openalex"],
        "primary_db": "patentsview",
        "want_fulltext": False,
        "note": (
            "The question that motivates the whole pipeline. Patents carry no DOI "
            "and are invisible to every scholarly index, so this chain has to "
            "start somewhere the scholarly indexes cannot reach."
        ),
    },
    "entity_identity": {
        "chain": ["pubchem", "openalex"],
        "primary_db": "pubchem",
        "want_fulltext": False,
        "note": (
            "Resolving a name to a stable registry identifier. This is what "
            "upgrades a claim from 'a paper said so' to 'this is the entity, here "
            "is its identifier' -- see ledger.py for why that distinction is "
            "load-bearing."
        ),
    },
    "biomed_clinical": {
        "chain": ["europepmc", "pubmed", "semantic_scholar"],
        "primary_db": "europepmc",
        "want_fulltext": False,
        "note": (
            "Life-sciences questions. Europe PMC also indexes preprints, which "
            "matters when the field is moving faster than journals publish."
        ),
    },
    "breadth_sweep": {
        "chain": [
            "openalex",
            "semantic_scholar",
            "europepmc",
            "crossref",
            "patentsview",
            "core",
        ],
        "primary_db": "openalex",
        "want_fulltext": True,
        "note": (
            "Deliberate over-retrieval for a saturation run: keep going until "
            "consecutive rounds stop producing anything new. Expensive on "
            "purpose. Pair with min_sources so the early-stop cannot short it."
        ),
    },
}

DEFAULT_INTENT = "discovery"

# ---------------------------------------------------------------------------
# Scopes: a coarse dial for callers who do not want to name an intent
# ---------------------------------------------------------------------------
SCOPE_TO_INTENTS: dict[str, list[str]] = {
    "all": ["discovery", "patent_prior_art", "entity_identity", "fulltext_method"],
    "literature": ["discovery", "fulltext_method", "citation_graph"],
    "patents": ["patent_prior_art"],
    "entities": ["entity_identity"],
    "biomed": ["biomed_clinical", "discovery"],
}

# ---------------------------------------------------------------------------
# Seat profiles
# ---------------------------------------------------------------------------
# Which intents a given board seat is allowed to spend retrieval budget on.
# A seat asking a question outside its profile is usually a sign the board is
# mis-specified, not that the router is wrong.
#
# The seat keys below match boards/example_board.yaml. The board is deliberately
# configuration: swapping in a different roster does not touch this file's
# structure, only its contents.
SEAT_PROFILES: dict[str, list[str]] = {
    "lead_investigator": ["discovery", "citation_graph"],
    "mechanism_analyst": ["discovery", "fulltext_method"],
    "methods_specialist": ["fulltext_method"],
    "modeling_specialist": ["fulltext_method", "discovery"],
    "measurement_specialist": ["fulltext_method", "entity_identity"],
    "domain_generalist": ["discovery"],
    "reproducibility_reviewer": ["fulltext_method", "citation_graph"],
    "statistics_reviewer": ["fulltext_method"],
    "external_validity_reviewer": ["citation_graph", "discovery"],
    "systems_engineer": ["fulltext_method", "discovery"],
    "integration_engineer": ["discovery"],
    "reliability_engineer": ["fulltext_method", "discovery"],
    "process_engineer": ["fulltext_method"],
    "cost_analyst": ["discovery"],
    "prior_art_analyst": ["patent_prior_art", "discovery"],
    "standards_analyst": ["discovery", "entity_identity"],
    "dossier_editor": ["discovery", "citation_graph"],
}

# Forgiving aliases. Callers get seat names slightly wrong constantly, and a
# silent fall-through to the default intent hides the mistake.
SEAT_ALIASES: dict[str, str] = {
    "lead": "lead_investigator",
    "pi": "lead_investigator",
    "mechanism": "mechanism_analyst",
    "methods": "methods_specialist",
    "modeling": "modeling_specialist",
    "modelling": "modeling_specialist",
    "measurement": "measurement_specialist",
    "metrology": "measurement_specialist",
    "generalist": "domain_generalist",
    "reproducibility": "reproducibility_reviewer",
    "stats": "statistics_reviewer",
    "statistics": "statistics_reviewer",
    "external_validity": "external_validity_reviewer",
    "systems": "systems_engineer",
    "integration": "integration_engineer",
    "reliability": "reliability_engineer",
    "process": "process_engineer",
    "cost": "cost_analyst",
    "prior_art": "prior_art_analyst",
    "patents": "prior_art_analyst",
    "standards": "standards_analyst",
    "editor": "dossier_editor",
    "qa": "dossier_editor",
}

# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------
# The single most effective cost control in the system, because it bounds spend
# *before* any tokens are generated rather than truncating afterwards.
#
# thin_hits   : enough results to stop walking the chain
# min_sources : floor on sources actually queried, so early-stop cannot skip a
#               breadth sweep down to one database
# pack_chars  : ceiling on the flattened context block; roughly 4 chars/token
BUDGETS: dict[str, dict[str, Any]] = {
    "lean": {
        "max_records": 6,
        "per_source": 5,
        "want_fulltext_for": 0,
        "excerpt_chars": 400,
        "pack_chars": 4_000,
        "thin_hits": 1,
        "min_sources": 1,
    },
    "standard": {
        "max_records": 15,
        "per_source": 10,
        "want_fulltext_for": 3,
        "excerpt_chars": 600,
        "pack_chars": 12_000,
        "thin_hits": 5,
        "min_sources": 1,
    },
    "deep": {
        "max_records": 40,
        "per_source": 20,
        "want_fulltext_for": 8,
        "excerpt_chars": 900,
        "pack_chars": 30_000,
        "thin_hits": 20,
        "min_sources": 3,
    },
}

DEFAULT_BUDGET = "standard"


def budget(name: str | None) -> dict[str, Any]:
    """Look up a budget preset, falling back to standard on anything unknown."""
    return dict(BUDGETS.get(name or DEFAULT_BUDGET, BUDGETS[DEFAULT_BUDGET]))


def canonical_seat(seat: str | None) -> str | None:
    if not seat:
        return None
    key = seat.strip().lower().replace("-", "_").replace(" ", "_")
    if key in SEAT_PROFILES:
        return key
    return SEAT_ALIASES.get(key)


def resolve_intents(
    intent: str | list[str] | None = None,
    scope: str | None = None,
    seat: str | None = None,
) -> list[str]:
    """Decide which intents a query should run under.

    Precedence, most specific first: an explicit intent beats a scope, which
    beats a seat profile, which beats the default. Unknown intent names are
    filtered out rather than raising -- a typo should degrade to the default
    behaviour, not abort a long run.
    """
    if intent:
        requested = [intent] if isinstance(intent, str) else list(intent)
        valid = [i for i in requested if i in INTENTS]
        if valid:
            return valid
        # Everything requested was invalid. Fall through to the next tier
        # rather than silently returning nothing.

    if scope:
        mapped = SCOPE_TO_INTENTS.get(scope.strip().lower())
        if mapped:
            return [i for i in mapped if i in INTENTS]

    canonical = canonical_seat(seat)
    if canonical:
        mapped = SEAT_PROFILES.get(canonical)
        if mapped:
            return [i for i in mapped if i in INTENTS]

    return [DEFAULT_INTENT]
