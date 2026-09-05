# Sources

Nine connectors behind one signature. Adding a tenth means writing one function and adding one registry entry.

Choosing them meant evaluating databases on three axes that pull against each other: **coverage** (does it contain the material?), **access terms** (am I permitted to query it this way?), and **cost** (what does breadth actually price out at?). A source can be excellent on the first and disqualified on the second.

---

## Wired up

| Source | Covers | Auth | Documented limit | Why it's here |
|---|---|---|---|---|
| **OpenAlex** | Scholarly works, broad | none (mailto recommended) | ~10 req/s polite pool | Default primary for discovery. Broadest keyless coverage; if you wire up one database, wire up this one. |
| **Semantic Scholar** | Works + citation graph | none (key raises limits) | heavily shaped without a key | The only free source giving a one-line `tldr` and *influential* citation counts — citations that built on a paper vs. merely mentioned it. |
| **Crossref** | DOI registry | none (mailto) | polite pool | Identifier arbiter, not a discovery engine. |
| **Europe PMC** | Life sciences + **preprints** | none | — | Official keyword-searchable preprint index; bioRxiv/medRxiv reachable through the same interface. |
| **PubMed** | Biomedical citations | none (key raises limits) | 3 req/s without a key | Identifier-grade records. `esummary` carries no abstracts — that's what Europe PMC is for. |
| **CORE** | Open-access **full text** | `CORE_API_KEY` | ~1 req/s free tier | The only source returning whole papers. Methods never survive into abstracts. |
| **PatentsView** | Granted US patents | `PATENTSVIEW_API_KEY` | — | Reaches material no scholarly index can see. |
| **PubChem** | Entity registry | none | ≤5 req/s | Identifier resolution — what upgrades a claim from EST to REAL. |
| **Unpaywall** | OA links for a known DOI | `CONTACT_EMAIL` required | — | Enrichment only, not a chain member. |

---

## Notes that cost real time to learn

**Crossref will always answer.** Its relevance search returns a confident-looking best match even for a paper it has never seen. Accepting that attaches the *wrong* DOI to a record — and a wrong identifier is far worse than a missing one, because everything downstream treats identifiers as ground truth. Hence a 0.85 title-similarity gate on resolution: genuine matches with subtitle drift land above 0.90, unrelated best-guesses rarely clear 0.7. `resolve_doi` returns `None` rather than a guess.

**Patent search must be OR, not AND.** Patent drafters choose deliberately broad language, so the vocabulary rarely matches a researcher's phrasing term for term. An ALL query against patents reliably returns nothing — and "nothing" reads as *the field is clear*, which is the single most dangerous false negative this system can produce.

**Patents have no DOIs** and are invisible to every scholarly index here. Dedup falls through to the patent number as entity id. That's correct: patent numbers are stronger identifiers than DOIs, they just aren't DOIs.

**The legacy PatentsView endpoint is gone.** `api.patentsview.org` answers 410; the current service is `search.patentsview.org/api/v1` and requires a key.

**OpenAlex returns abstracts inside out** — an inverted index of word → positions, not a string. Reconstruction happens at the connector boundary, which is the whole reason connectors exist: nothing downstream should know that.

**An invalid `select` field is a hard 400, not a warning.** `host_venue` was removed from the OpenAlex API in favour of `primary_location`, and requesting it rejects the entire query. The failure is nastier than it sounds: the connector goes down completely, the chain falls through to a fallback, and results still come back — so the run *looks* fine while the designated primary source is contributing nothing. Caught here only by watching the retry and failure counters on a live run, which is why `savings_report()` prints them.

**Unauthenticated Semantic Scholar will 429 regardless of politeness.** Its free tier is shaped against a shared global pool, so backing off to a 3-second interval reduces rejections but cannot eliminate them. This is expected, and the chain falling through to another source is the designed response — not a bug to be retried harder.

**Permanent failures should not be retried.** A 400/410/422 fails identically on the second attempt. Retrying wastes the run's time and hides how quickly the source actually rejected you, so `SourceUnavailable` carries a `retryable` flag and the router breaks out immediately. It still counts toward the circuit breaker.

**Preprints need their own query.** Europe PMC preprints are fetched as a second call rather than folded in with an `OR`, because in a single query the published literature crowds them out entirely. In a fast-moving field the thing that pre-empts an idea is frequently a four-month-old preprint no journal index has picked up.

**Implicit-AND engines need relaxation.** A precise eight-term query returns zero, and zero is indistinguishable from "no such literature" unless you retry shorter.

**Entity databases index things, not questions.** A question must be decomposed into candidate entity names first — and that decomposition is capped at 5 variants. Without the cap, junk terms each fanned out into roughly fifteen requests and flooded a provider. That incident produced both `MAX_VARIANTS` and the shared rate governor.

---

## Deliberately not automated

Some databases that would genuinely improve coverage are absent because **their terms of use do not permit programmatic querying**. Subscription chemical-abstract services, several standards bodies, and most regulatory portals fall here — some prohibit automated access outright, others simply publish no API.

These are handled as a **human queue**: the pipeline emits the exact search strings, a person runs them by hand, and the results return through the same staging path as everything else, carrying the same identifier requirements.

This is a design position. A pipeline that quietly scrapes a subscription database produces results its owner cannot publish, cannot cite in a filing, and cannot defend if asked where they came from. The queue is slower and correct.

---

## Adding a source

```python
def search(query, per_source=10, want_fulltext=False, excerpt_chars=600, **_):
    payload = http_get("provider_key", URL, params={...})   # throttled; raises SourceUnavailable
    return [make_record("provider_key", item["title"], doi=..., authority="indexed")
            for item in payload["results"]]
```

Then add it to `default_adapters()`, `SOURCE_META`, an intent chain in `profiles.py`, a throttle interval in `throttle.py`, and a fake in `testing.py`.

Two rules the base layer enforces:

- **Not configured is not an error.** A key-gated source with no key returns `[]` and the chain falls through. A fresh clone with zero credentials must still work.
- **Transport failure raises; an empty answer returns.** `SourceUnavailable` for timeout / 5xx / rate limit / malformed payload. An empty list means the source answered and had nothing. Never collapse these.

---

## Extending to other domains

The nine shipped connectors are scholarly and patent sources because that is what the author needed. Nothing in the architecture is specific to them. A corporate, legal, or internal source is the same function with a different parser.

The only decision that carries weight is **`authority` and `entity_id`**, because those determine whether a claim can be graded REAL. Get that right and the rest is parsing.

### Worked example: SEC EDGAR full-text search

A corporate filing is a registry object, not a document about one — so it resolves to REAL.

```python
# src/run_engine/retrieval/sources/edgar.py
from ..models import Record
from ..query import excerpt_around, keywordize
from .base import clean, contact_email, http_get, make_record

BASE = "https://efts.sec.gov/LATEST/search-index?q="
PROVIDER = "edgar"

# SEC requires a declared User-Agent with contact details and asks for
# <=10 req/s. base.user_agent() already sends CONTACT_EMAIL; the throttle
# interval belongs in throttle.DEFAULT_INTERVALS as "edgar": 0.10.

def search(query, per_source=10, want_fulltext=False, excerpt_chars=600, **_):
    if not contact_email():
        return []                       # SEC requires identification: skip, don't fail

    terms = keywordize(query)
    payload = http_get(PROVIDER, BASE, params={"q": terms, "forms": "10-K,10-Q,8-K"})
    if not payload:
        return []

    out = []
    for hit in payload.get("hits", {}).get("hits", [])[:per_source]:
        src = hit.get("_source", {})
        accession = src.get("adsh")                 # 0000320193-24-000123
        cik = (src.get("ciks") or [None])[0]

        rec = make_record(
            PROVIDER,
            src.get("display_names", [None])[0],
            # A registry identifier -> ledger.grade_for() returns REAL.
            entity_id=accession or (f"CIK: {cik}" if cik else None),
            url=f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}.txt",
            year=int(str(src.get("file_date", ""))[:4] or 0) or None,
            venue=src.get("file_type"),
            abstract=clean(src.get("summary")),
            excerpt=excerpt_around(src.get("summary") or "", terms, excerpt_chars),
            # The filing IS the record. Nothing outranks it on what was filed.
            authority="primary_db",
        )
        if rec:
            out.append(rec)
    return out
```

Wire it up in four places:

```python
# sources/__init__.py
def default_adapters():
    return {..., "edgar": edgar.search}

# profiles.py -- a new intent, or extend an existing chain
INTENTS["corporate_filings"] = {
    "chain": ["edgar", "openalex"],     # filing first, commentary second
    "primary_db": "edgar",
    "want_fulltext": True,
    "note": "What the company told the regulator, before what anyone said about it.",
}

# throttle.py
DEFAULT_INTERVALS["edgar"] = 0.10       # SEC asks for <=10 req/s

# testing.py -- so no test can reach the live API
def fake_adapters(calls=None):
    return {..., "edgar": fake_source("edgar", authority="primary_db", calls=calls)}
```

And add the identifier namespace if it isn't already there — for EDGAR it is:

```python
# ledger.py
Namespace("EDGAR accession", "corporate filings", "registry",
          r"\b\d{10}-\d{2}-\d{6}\b", "0000320193-24-000123"),
```

### Other domains, same shape

| Source | `entity_id` | `authority` | Note |
|---|---|---|---|
| **Court archives** (PACER, CourtListener) | docket number | `primary_db` | The docket is the case. Opinions about it are `indexed`. |
| **Federal Register / eCFR** | CFR citation | `primary_db` | A summary of a rule is not the rule. |
| **Internal document store** | your own stable doc id | `primary_db` | Add the id format to `IDENTIFIER_NAMESPACES` or it cannot be promoted. |
| **Company registries** (Companies House, state SoS) | registration number | `primary_db` | Resolving the entity is the whole job. |
| **Trade press / analyst notes** | DOI if any, else none | `weak` | Deliberately weak. Commentary is not a filing, and repetition across outlets is not corroboration. |

That last row is the one people get wrong. Secondary sources agreeing with each other feels like evidence and usually traces back to a single origin. Grading them `weak` means they inform the analysis and cannot be promoted as findings — which is the correct treatment.

### Enterprise sources with no public API

Same rule as the subscription databases above: if the terms don't permit programmatic access, run it as a **human queue**. Emit the exact query strings, have a person run them, and bring results back through `StagingLedger.stage()` with `origin="manual:<source>"`.

The identifier requirement does not relax for hand-collected material. A human-returned row without a resolvable identifier is still WEAK and still unpromotable — otherwise the queue becomes the hole in the discipline, and everyone learns to route around the gate through it.
