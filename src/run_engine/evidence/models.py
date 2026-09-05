"""Core data types.

One normalized ``Record`` shape across every connector. Each source speaks a
different dialect -- OpenAlex returns inverted abstract indexes, Europe PMC
returns XML-ish JSON, PatentsView has no DOI at all -- and normalizing at the
connector boundary is what lets the router dedupe and rank across them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

# Authority tiers, highest first. Used to decide which copy of a duplicated
# record survives a merge. A record from the database the intent designated as
# authoritative outranks a merely-indexed copy, which outranks an open-access
# mirror, which outranks anything we could not classify.
AUTHORITY_ORDER = ("primary_db", "indexed", "oa", "weak")


@dataclass
class Record:
    """A single retrieved item: paper, preprint, patent, or entity."""

    title: str
    source: str                       # connector key that produced this
    url: str | None = None
    doi: str | None = None
    pmid: str | None = None
    entity_id: str | None = None      # structured id (e.g. OpenAlex W…, PubChem CID)
    year: int | None = None
    venue: str | None = None
    authors: list[str] = field(default_factory=list)
    abstract: str | None = None
    tldr: str | None = None           # one-line summary when the source offers one
    excerpt: str | None = None        # query-centred full-text window
    citation_count: int | None = None
    open_access_pdf: str | None = None
    authority: str = "weak"
    role: str = "primary"             # "primary" or "fallback" -- set by the router
    extra: dict[str, Any] = field(default_factory=dict)

    def authority_rank(self) -> int:
        try:
            return AUTHORITY_ORDER.index(self.authority)
        except ValueError:
            return len(AUTHORITY_ORDER)

    def content_score(self) -> int:
        """How much usable text this record carries. Ties break toward content."""
        return sum(
            bool(x) for x in (self.excerpt, self.abstract, self.tldr, self.open_access_pdf)
        )

    def best_text(self, limit: int) -> str:
        """Cheapest-informative text, in preference order.

        A one-line TLDR is worth more per token than a truncated abstract, and a
        query-centred full-text excerpt is worth more than either. Ordering here
        is the single biggest lever on token spend downstream.
        """
        for candidate in (self.tldr, self.excerpt, self.abstract):
            if candidate:
                text = " ".join(candidate.split())
                return text[:limit]
        return ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchResult:
    """What a routed query returns. Never raises on partial failure."""

    query: str
    intent: str
    records: list[Record] = field(default_factory=list)
    sources_queried: list[str] = field(default_factory=list)
    fallbacks_used: list[str] = field(default_factory=list)
    breaker_tripped: list[str] = field(default_factory=list)
    # Reformulations attempted after a dead end, in order. Surfaced rather than
    # hidden: "we found nothing" and "we found nothing after trying it four
    # ways" are different claims, and only the second one is worth acting on.
    reformulations: list[dict[str, str]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.records)

    def pack(self, pack_chars: int, excerpt_chars: int) -> str:
        """Flatten to a bounded text block suitable for an LLM context slot."""
        out: list[str] = []
        used = 0
        for i, rec in enumerate(self.records, start=1):
            cite = rec.doi or rec.pmid or rec.entity_id or rec.url or "no-id"
            head = f"[{i}] {rec.title} ({rec.year or 'n.d.'}; {rec.source}; {cite})"
            body = rec.best_text(excerpt_chars)
            chunk = f"{head}\n{body}\n" if body else f"{head}\n"
            if used + len(chunk) > pack_chars:
                break
            out.append(chunk)
            used += len(chunk)
        return "\n".join(out)
