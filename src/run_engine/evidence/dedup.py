"""Deduplication at two levels.

**Record dedup** is deterministic and always on. The same paper reaches you from
four databases under four different shapes, and the one you keep should be the
most authoritative copy, not whichever happened to arrive first.

**Topic dedup** is semantic and optional. Over a long run the question generator
starts producing rephrasings of questions already answered -- "thermal stability
of X" and "how stable is X at temperature" are one question. Catching that needs
embeddings, so it degrades to pass-through when no embedding key is configured.
Degrading is deliberate: a missing optional dependency should cost you some
redundant work, not the run.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable

from .models import Record
from ..retrieval.query import normalize_title

# ---------------------------------------------------------------------------
# Record dedup
# ---------------------------------------------------------------------------

_DOI = re.compile(r"10\.\d{4,9}/\S+")


def normalize_doi(doi: str | None) -> str | None:
    """Strip the many prefixes a DOI arrives wearing."""
    if not doi:
        return None
    match = _DOI.search(doi.strip().lower())
    return match.group(0).rstrip(".,;)") if match else None


def cluster_key(rec: Record) -> str:
    """The identity of a record, in descending order of trustworthiness.

    A DOI is a real identifier and settles the question. A registry identifier
    (PMID, entity id) is nearly as good. A normalized title is a last resort:
    it collapses genuine duplicates but will also, occasionally, collapse two
    distinct papers that share a title. That trade is worth making at the tail
    but should never outrank a real identifier.
    """
    doi = normalize_doi(rec.doi)
    if doi:
        return f"doi:{doi}"
    if rec.pmid:
        return f"pmid:{str(rec.pmid).strip()}"
    if rec.entity_id:
        return f"entity:{str(rec.entity_id).strip().lower()}"
    title = normalize_title(rec.title)
    if title:
        return f"title:{title}"
    return f"raw:{id(rec)}"


def _better(candidate: Record, incumbent: Record) -> bool:
    """Should ``candidate`` replace ``incumbent`` as the surviving copy?"""
    if candidate.authority_rank() != incumbent.authority_rank():
        return candidate.authority_rank() < incumbent.authority_rank()
    if candidate.content_score() != incumbent.content_score():
        return candidate.content_score() > incumbent.content_score()
    cand_cites = candidate.citation_count or 0
    inc_cites = incumbent.citation_count or 0
    return cand_cites > inc_cites


def merge_records(records: Iterable[Record]) -> list[Record]:
    """Collapse duplicates, keeping the best copy and back-filling identifiers.

    Back-filling matters as much as collapsing: the authoritative copy of a
    record often lacks the open-access link that the weaker copy carried. Losing
    that link because the weaker record lost the ranking would be a real
    regression, so surviving records absorb missing fields from the ones they
    displace.
    """
    best: dict[str, Record] = {}
    order: list[str] = []

    for rec in records:
        key = cluster_key(rec)
        if key not in best:
            best[key] = rec
            order.append(key)
            continue
        incumbent = best[key]
        winner, loser = (rec, incumbent) if _better(rec, incumbent) else (incumbent, rec)
        # Absorb anything the winner is missing.
        for field in (
            "doi", "pmid", "entity_id", "url", "abstract", "tldr", "excerpt",
            "open_access_pdf", "venue", "year", "citation_count",
        ):
            if not getattr(winner, field, None) and getattr(loser, field, None):
                setattr(winner, field, getattr(loser, field))
        if not winner.authors and loser.authors:
            winner.authors = loser.authors
        best[key] = winner

    merged = [best[k] for k in order]
    merged.sort(key=lambda r: (r.authority_rank(), -r.content_score(), -(r.citation_count or 0)))
    return merged


# ---------------------------------------------------------------------------
# Topic dedup (optional, embedding-based)
# ---------------------------------------------------------------------------

COSINE_THRESHOLD = 0.80
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMS = 512


class TopicDeduper:
    """Near-duplicate detection over research questions.

    Cache format is JSON keys plus a NumPy ``.npy`` matrix -- deliberately not
    pickle. A cache file is data that gets read back and trusted; pickle turns
    that into arbitrary code execution, and there is no reason to accept that
    risk for a float array.
    """

    def __init__(self, cache_dir: str | Path | None = None, threshold: float = COSINE_THRESHOLD):
        self.threshold = threshold
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._keys: list[str] = []
        self._vecs = None
        self._np = None
        self._enabled = bool(os.environ.get("OPENAI_API_KEY"))
        if self._enabled:
            try:
                import numpy  # noqa: F401
                self._np = numpy
            except ImportError:
                # numpy is an optional extra. Without it, degrade rather than fail.
                self._enabled = False
        if self._enabled and self.cache_dir:
            self._load_cache()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _load_cache(self) -> None:
        assert self.cache_dir is not None and self._np is not None
        keys_path = self.cache_dir / "topics.json"
        vecs_path = self.cache_dir / "topics.npy"
        if keys_path.is_file() and vecs_path.is_file():
            try:
                self._keys = json.loads(keys_path.read_text(encoding="utf-8"))
                self._vecs = self._np.load(vecs_path)
            except (OSError, ValueError, json.JSONDecodeError):
                self._keys, self._vecs = [], None

    def save(self) -> None:
        if not (self._enabled and self.cache_dir and self._vecs is not None):
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "topics.json").write_text(json.dumps(self._keys), encoding="utf-8")
        self._np.save(self.cache_dir / "topics.npy", self._vecs)

    def _embed(self, texts: list[str]):
        """Embed via the configured provider. Returns None on any failure."""
        try:
            import requests
            resp = requests.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                json={"model": EMBED_MODEL, "input": texts, "dimensions": EMBED_DIMS},
                timeout=30,
            )
            if resp.status_code != 200:
                return None
            vecs = [item["embedding"] for item in resp.json()["data"]]
            arr = self._np.asarray(vecs, dtype="float32")
            norms = self._np.linalg.norm(arr, axis=1, keepdims=True)
            return arr / self._np.clip(norms, 1e-9, None)
        except Exception:
            return None

    def filter_new(self, topics: list[str]) -> list[str]:
        """Return only topics that are new against the store *and each other*.

        Checking within the incoming batch matters: a generator asked for thirty
        questions will happily hand you the same question three ways in one
        response, and comparing only against history would let all three through.
        """
        if not topics:
            return []
        if not self._enabled:
            seen: set[str] = set(k.lower() for k in self._keys)
            out: list[str] = []
            for topic in topics:
                low = " ".join(topic.lower().split())
                if low not in seen:
                    seen.add(low)
                    out.append(topic)
                    self._keys.append(topic)
            return out

        embedded = self._embed(topics)
        if embedded is None:
            self._enabled = False
            return self.filter_new(topics)

        fresh: list[str] = []
        for i, topic in enumerate(topics):
            vec = embedded[i : i + 1]
            if self._vecs is not None and len(self._vecs):
                if float(self._np.max(self._vecs @ vec.T)) >= self.threshold:
                    continue
            self._vecs = vec if self._vecs is None else self._np.vstack([self._vecs, vec])
            self._keys.append(topic)
            fresh.append(topic)
        return fresh


class SaturationTracker:
    """Decides when a discovery run has stopped learning.

    Counting to a fixed target ("find 50 papers") consistently misses the tail:
    you stop while new material is still arriving, or you grind long after it
    stopped. The better signal is *consecutive dry rounds* -- rounds that
    produced nothing new. Two in a row is the default, which in practice trades
    one wasted round for confidence that the well is actually dry.
    """

    def __init__(self, dry_rounds: int = 2) -> None:
        self.dry_rounds = dry_rounds
        self._consecutive_dry = 0
        self.rounds = 0
        self.total_new = 0

    def record(self, new_items: int) -> None:
        self.rounds += 1
        self.total_new += new_items
        self._consecutive_dry = 0 if new_items > 0 else self._consecutive_dry + 1

    @property
    def saturated(self) -> bool:
        return self._consecutive_dry >= self.dry_rounds

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return (
            f"SaturationTracker(rounds={self.rounds}, new={self.total_new}, "
            f"dry={self._consecutive_dry}/{self.dry_rounds}, saturated={self.saturated})"
        )
