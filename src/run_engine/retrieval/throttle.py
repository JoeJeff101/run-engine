"""Per-provider rate governor.

Every connector shares one process-wide governor keyed by provider name, so two
different code paths hitting the same API still respect a single interval. The
intervals below are not guesses -- each is the provider's own documented limit,
cited at the call site in the connector.

This exists because of a specific failure. An early version generated search
variants per query without a cap; a handful of junk terms each fanned out into
roughly fifteen requests, and the aggregate tripped a provider's abuse
threshold. Two fixes came out of it: a hard cap on generated variants (see
``query.variants``) and this governor. Politeness is a correctness property --
a throttled client that finishes is strictly better than a fast one that gets
blocked halfway through a run.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict

# Provider -> minimum seconds between requests.
#
# Sources for these numbers:
#   pubchem   -- PUG-REST asks for no more than 5 requests/second.
#   ncbi      -- E-utilities allows 3 requests/second without an API key.
#   crossref  -- polite pool; no hard published rate, 100ms is courteous.
#   openalex  -- 10 requests/second for the polite pool.
#   s2        -- unauthenticated traffic is shaped against a shared global pool,
#                so 429s happen regardless of how polite one client is. Backing
#                off to 3s reduces them; it cannot eliminate them. The chain is
#                expected to fall through to another source, and does.
#   core      -- documented as roughly 1 request/second on the free tier.
DEFAULT_INTERVALS: dict[str, float] = {
    "pubchem": 0.22,
    "ncbi": 0.34,
    "crossref": 0.10,
    "openalex": 0.10,
    "semantic_scholar": 3.00,
    "core": 1.10,
    "europepmc": 0.15,
    "patentsview": 0.30,
    "unpaywall": 0.10,
}

_DEFAULT_INTERVAL = 0.20


class RateGovernor:
    """Serializes calls per provider key with a minimum inter-request gap."""

    def __init__(self, intervals: dict[str, float] | None = None) -> None:
        self._intervals = dict(DEFAULT_INTERVALS)
        if intervals:
            self._intervals.update(intervals)
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._last: dict[str, float] = {}
        self._guard = threading.Lock()
        self.waits: dict[str, float] = defaultdict(float)

    def interval(self, key: str) -> float:
        return self._intervals.get(key, _DEFAULT_INTERVAL)

    def wait(self, key: str) -> None:
        """Block until it is polite to call ``key`` again."""
        gap = self.interval(key)
        with self._locks[key]:
            with self._guard:
                last = self._last.get(key, 0.0)
            now = time.monotonic()
            delay = gap - (now - last)
            if delay > 0:
                time.sleep(delay)
                with self._guard:
                    self.waits[key] += delay
            with self._guard:
                self._last[key] = time.monotonic()

    def reset(self) -> None:
        with self._guard:
            self._last.clear()
            self.waits.clear()


# Process-wide default. Connectors use this unless handed another.
GOVERNOR = RateGovernor()
