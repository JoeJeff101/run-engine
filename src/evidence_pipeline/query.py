"""Turning a research question into per-database queries.

A researcher's question and a database's query language are different things.
"What is documented about the thermal stability of solid-state electrolyte
interfaces?" is a good question and a terrible query: half its tokens are
research-paper connective tissue that appears in every abstract ever written,
and the databases disagree about what to do with the rest.

So there are two jobs here. ``keywordize`` strips the question down to signal.
Then each connector reshapes that signal into its own dialect, because the same
concept has to be asked for differently depending on who is answering:

* Implicit-AND engines return zero hits for a long term list, so terms get
  progressively relaxed until something comes back.
* Topic-search syntaxes treat a quoted string as an exact phrase, which is
  almost never what you want -- the wrapper must stay unquoted.
* Substance databases index *entities*, not questions, so a question has to be
  decomposed into candidate entity names before it can be looked up at all.
"""

from __future__ import annotations

import re

# Words that carry no discriminating power in a scholarly index. These are not
# general English stopwords -- "between" and "versus" survive, since they can be
# meaningful. What gets cut is research-question scaffolding: the words that
# appear in the *question* but never usefully in the *answer*.
STOPWORDS: frozenset[str] = frozenset(
    """
    a an and are as at be been being by for from has have how in into is it its
    of on or that the their there these this those to was were what when where
    which who why will with within about above across after again against all
    already also although among any because before below best both but can
    could did do does doing done during each either enough especially even ever
    every few first further get give given greater high higher however if
    important including indeed instead just known large larger last less like
    likely made main make many may might more most much must need needed new
    no nor not now often only other others our out over own particular per
    perhaps possible potential quite rather really recent recently relevant
    report reported reports research researched said same see seen several
    should show showed shown shows since so some specific still such sufficient
    take than then therefore they thing things think though through thus too
    toward towards typical typically under until up upon us use used useful
    uses using usually various very via want way well were whether while whose
    would documented quantitatively evidence literature study studies data
    understanding overview summary review current state art known unknown
    """.split()
)

# Short tokens that are real terms of art rather than noise, so the length
# filter must not eat them.
SHORT_KEEP: frozenset[str] = frozenset(
    {"ph", "uv", "ir", "nm", "mw", "ml", "hz", "dc", "ac", "ai", "ml", "3d", "2d"}
)

MAX_TERMS = 10

# A hard ceiling on generated query variants. This number is the direct result
# of an incident: without it, junk terms each fanned out into roughly fifteen
# requests and flooded a provider. See throttle.py for the other half of the fix.
MAX_VARIANTS = 5

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9\-']*")


def keywordize(question: str, max_terms: int = MAX_TERMS) -> str:
    """Reduce a natural-language question to a bounded keyword query.

    Falls back to the original question if filtering leaves too little to work
    with -- an over-aggressive filter that returns one word is worse than a
    slightly noisy query.
    """
    tokens = _TOKEN.findall(question or "")
    kept: list[str] = []
    seen: set[str] = set()
    for raw in tokens:
        low = raw.lower()
        if low in STOPWORDS:
            continue
        if len(low) < 3 and low not in SHORT_KEEP:
            continue
        if low in seen:
            continue
        seen.add(low)
        kept.append(low)
        if len(kept) >= max_terms:
            break
    if len(kept) < 2:
        return " ".join((question or "").split())
    return " ".join(kept)


def relax(query: str, steps: tuple[int, ...] = (6, 4, 3)) -> list[str]:
    """Progressive term relaxation for implicit-AND engines.

    Some full-text engines AND every term together. A precise eight-term query
    against those returns nothing, and "nothing" is indistinguishable from "no
    such literature" unless you retry shorter. Returns the full query followed
    by successively shorter prefixes, longest first, deduplicated.
    """
    terms = query.split()
    out = [query]
    for n in steps:
        if n < len(terms):
            candidate = " ".join(terms[:n])
            if candidate not in out:
                out.append(candidate)
    return out


def topic_wrap(query: str, field: str = "TS") -> str:
    """Wrap for a field-tagged topic search, deliberately unquoted.

    Quoting here would turn the query into an exact-phrase match, which reliably
    returns zero results for anything longer than three words. The un-quoted
    form is what actually behaves like a topic search.
    """
    return f"{field}=({query})"


def variants(query: str, max_variants: int = MAX_VARIANTS) -> list[str]:
    """Candidate lookup strings for an entity database.

    Entity databases index *things*, not questions. A question has to be turned
    into plausible entity names: the whole string, shorter prefixes, and
    adjacent word pairs. Capped hard -- see MAX_VARIANTS.
    """
    terms = query.split()
    out: list[str] = []

    def add(candidate: str) -> None:
        candidate = candidate.strip()
        if candidate and candidate not in out and len(out) < max_variants:
            out.append(candidate)

    add(query)
    for n in (4, 3, 2):
        if n < len(terms):
            add(" ".join(terms[:n]))
    for i in range(len(terms) - 1):
        add(" ".join(terms[i : i + 2]))
    return out[:max_variants]


def excerpt_around(text: str, query: str, width: int) -> str:
    """A query-centred window of full text.

    Full text is the most expensive thing to carry and the most valuable thing
    to have. The compromise: keep a window centred on the first query term that
    actually appears, and throw the rest away at parse time so it never occupies
    memory or a context slot.
    """
    if not text:
        return ""
    flat = " ".join(text.split())
    if width <= 0 or len(flat) <= width:
        return flat
    low = flat.lower()
    anchor = -1
    for term in query.split():
        pos = low.find(term.lower())
        if pos != -1:
            anchor = pos
            break
    if anchor == -1:
        return flat[:width]
    start = max(0, anchor - width // 3)
    return flat[start : start + width]


def normalize_title(title: str) -> str:
    """Aggressive title normalization, used only for last-resort dedup."""
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


# ---------------------------------------------------------------------------
# Dynamic reformulation
# ---------------------------------------------------------------------------
# When every source in a chain returns nothing, the wrong response is to run the
# same query again. Empty means one of three things, and they need different
# fixes:
#
#   too narrow      -- the conjunction of terms excludes everything. Broaden.
#   wrong register  -- the field calls it something else. Attack from the most
#                      distinctive term and let the database show you the
#                      surrounding vocabulary.
#   compound        -- it is really two questions, and no single document
#                      answers both. Split it.
#
# These transforms are deterministic and cheap, and they run before any model is
# asked to think. An agent-supplied reformulator can be plugged in on top for a
# genuine lateral pivot -- see `Router.search(reformulator=...)` -- but the
# cheap structural fixes should be exhausted first, because most dead ends are
# structural rather than conceptual.


def broaden(query: str, keep: int = 3) -> str:
    """Keep the highest-signal terms, drop the qualifiers.

    Signal is approximated by term length: in technical vocabulary the longer
    token is almost always the more discriminating one. A heuristic, but a
    stable one, and it costs nothing.
    """
    terms = query.split()
    if len(terms) <= keep:
        return query
    ranked = sorted(terms, key=lambda t: (-len(t), terms.index(t)))[:keep]
    return " ".join(sorted(ranked, key=terms.index))


def pivot_to_rare(query: str, keep: int = 2) -> str:
    """Query the most distinctive terms alone.

    The inverse move to broadening. Where broadening asks "what else is in this
    neighbourhood?", this asks "what does the literature call the neighbourhood
    around this one unusual thing?" It is how you recover from a vocabulary
    mismatch: you cannot guess the field's preferred term, but you can find
    documents containing your rare term and read theirs.
    """
    terms = query.split()
    if len(terms) <= keep:
        return query
    ranked = sorted(terms, key=lambda t: (-len(t), terms.index(t)))[:keep]
    return " ".join(sorted(ranked, key=terms.index))


def decompose(query: str) -> list[str]:
    """Split a compound question into overlapping halves.

    Overlapping, not disjoint: the shared pivot term keeps both halves anchored
    to the same subject. Two clean halves of a compound question frequently each
    have a literature even when their conjunction has none.
    """
    terms = query.split()
    if len(terms) < 4:
        return [query]
    mid = len(terms) // 2
    return [" ".join(terms[: mid + 1]), " ".join(terms[mid - 1 :])]


def reformulations(query: str) -> list[tuple[str, str]]:
    """Ordered (strategy, query) pairs to try after a dead end.

    Ordered cheapest-and-most-likely first. Deduplicated against the original,
    because re-running the query that just failed is the exact behaviour this
    exists to prevent.
    """
    out: list[tuple[str, str]] = []
    seen = {query.strip()}

    def add(strategy: str, candidate: str) -> None:
        candidate = " ".join(candidate.split())
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append((strategy, candidate))

    add("broaden", broaden(query))
    add("pivot_to_rare", pivot_to_rare(query))
    for half in decompose(query):
        add("decompose", half)
    return out
