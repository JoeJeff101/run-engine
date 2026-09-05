"""The intent-aware retrieval router.

Walks an intent's ranked source chain, stopping as soon as it has enough, and
degrading rather than failing when a source misbehaves.

Four behaviours are worth understanding before reading the code, because each
one exists in response to something that went wrong:

**Early stop.** Walking the whole chain when the primary already answered is
pure waste. The walk halts once ``thin_hits`` results are in hand *and*
``min_sources`` databases have been consulted. The second condition is not
redundant: a breadth sweep whose first source is productive would otherwise
collapse to a single-database query, which is exactly the failure mode a breadth
sweep exists to prevent.

**Circuit breaker.** A source that has failed twice in a row is not going to
succeed on the ninth query either. After two consecutive exhausted-retry
failures it is cut for the remainder of the run. Without this, one provider
having a bad afternoon turns a twenty-minute run into an hour of timeouts.

**Cache.** Keyed on the *normalized* query, so two differently-worded questions
that reduce to the same keywords share a cache entry. Fourteen-day TTL: long
enough to make an iterative session cheap, short enough that a literature search
never silently serves last quarter's answer.

**Never raise.** Total failure returns an empty ``ResearchResult``, not an
exception. A long autonomous run must not die because one database returned a
malformed payload at hour three.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterable

from .models import Record, ResearchResult
from .profiles import INTENTS, budget as budget_preset, resolve_intents
from .query import keywordize

Adapter = Callable[..., Iterable[Record]]

CACHE_TTL_SECONDS = 14 * 24 * 3600
DEFAULT_MAX_RETRIES = 2
DEFAULT_BREAKER_THRESHOLD = 2


class SourceUnavailable(Exception):
    """Raised by an adapter when a source is reachable but cannot answer.

    Deliberately distinct from "the source answered, and the answer is nothing".
    Conflating the two records a false negative -- "no prior art exists" when the
    truth is "the patent database timed out" -- and that is the single most
    dangerous error this system can make.

    ``retryable=False`` marks a permanent failure: a malformed request, a removed
    field, a retired endpoint. Retrying those is pure waste -- the second attempt
    fails identically -- and worse, the retry delay hides how fast the source
    actually rejected you.
    """

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class Router:
    def __init__(
        self,
        adapters: dict[str, Adapter] | None = None,
        cache_dir: str | Path | None = None,
        cache_ttl: int = CACHE_TTL_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        breaker_threshold: int = DEFAULT_BREAKER_THRESHOLD,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if adapters is None:
            from .sources import default_adapters
            adapters = default_adapters()
        self.adapters = dict(adapters)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_ttl = cache_ttl
        self.max_retries = max_retries
        self.breaker_threshold = breaker_threshold
        self._sleep = sleep
        self._failures: dict[str, int] = {}
        self._tripped: set[str] = set()
        self.stats: dict[str, Any] = {}
        self.reset_run()

    # -- run lifecycle ----------------------------------------------------

    def reset_run(self) -> None:
        """Clear breaker state and per-run counters. Call between runs."""
        self._failures.clear()
        self._tripped.clear()
        self.stats = {
            "live_calls": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "records_fetched": 0,
            "records_kept": 0,
            "retries": 0,
            "adapter_failures": 0,
        }

    # -- cache ------------------------------------------------------------

    def _cache_path(self, source: str, query: str, per_source: int, max_records: int) -> Path | None:
        if not self.cache_dir:
            return None
        raw = f"{source}|{keywordize(query)}|{per_source}|{max_records}"
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{source}-{digest}.json"

    def _cache_read(self, path: Path | None) -> list[Record] | None:
        if not path or not path.is_file():
            return None
        try:
            if time.time() - path.stat().st_mtime > self.cache_ttl:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            return [Record(**item) for item in payload]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A corrupt cache entry is a cache miss, never a crash.
            return None

    def _cache_write(self, path: Path | None, records: list[Record]) -> None:
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps([asdict(r) for r in records], ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(path)  # atomic; a reader never sees a half-written file
        except OSError:
            pass

    # -- one source -------------------------------------------------------

    def _call_source(
        self,
        source: str,
        query: str,
        per_source: int,
        max_records: int,
        want_fulltext: bool,
        excerpt_chars: int,
    ) -> list[Record]:
        """Call one adapter with retry, backoff, cache, and breaker accounting."""
        if source in self._tripped:
            return []
        adapter = self.adapters.get(source)
        if adapter is None:
            return []

        path = self._cache_path(source, query, per_source, max_records)
        cached = self._cache_read(path)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return cached
        if path is not None:
            self.stats["cache_misses"] += 1

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                self.stats["live_calls"] += 1
                records = list(
                    adapter(
                        query=query,
                        per_source=per_source,
                        want_fulltext=want_fulltext,
                        excerpt_chars=excerpt_chars,
                    )
                )
                self._failures[source] = 0
                self._cache_write(path, records)
                return records
            except Exception as exc:  # adapters may raise anything a client library does
                last_error = exc
                if not getattr(exc, "retryable", True):
                    break  # permanent: a second identical request fails identically
                if attempt < self.max_retries:
                    self.stats["retries"] += 1
                    # Exponential backoff with jitter. The jitter matters when
                    # several workers hit the same provider: without it they
                    # retry in lockstep and re-trigger the same rate limit.
                    self._sleep(0.5 * (2**attempt) + random.uniform(0, 0.4))

        # Retries exhausted.
        self.stats["adapter_failures"] += 1
        self._failures[source] = self._failures.get(source, 0) + 1
        if self._failures[source] >= self.breaker_threshold:
            self._tripped.add(source)
        _ = last_error
        return []

    # -- chain walk -------------------------------------------------------

    def _run_chain(
        self,
        question: str,
        intent: str,
        limits: dict[str, Any],
    ) -> tuple[list[Record], list[str], list[str]]:
        spec = INTENTS[intent]
        chain: list[str] = list(spec["chain"])
        primary_db: str = spec["primary_db"]
        want_fulltext: bool = bool(spec.get("want_fulltext")) and limits["want_fulltext_for"] > 0

        collected: list[Record] = []
        queried: list[str] = []
        fallbacks: list[str] = []

        for index, source in enumerate(chain):
            if source not in self.adapters:
                continue
            if source in self._tripped:
                continue

            # Early stop: enough results AND enough breadth.
            if (
                len(collected) >= limits["thin_hits"]
                and len(queried) >= limits["min_sources"]
            ):
                break

            role = "primary" if index == 0 else "fallback"
            records = self._call_source(
                source=source,
                query=question,
                per_source=limits["per_source"],
                max_records=limits["max_records"],
                want_fulltext=want_fulltext,
                excerpt_chars=limits["excerpt_chars"],
            )
            queried.append(source)
            if role == "fallback" and records:
                fallbacks.append(source)

            self.stats["records_fetched"] += len(records)
            for rec in records:
                rec.role = role
                rec.source = rec.source or source
                if rec.authority == "weak":
                    rec.authority = "primary_db" if source == primary_db else "indexed"
                collected.append(rec)

        return collected, queried, fallbacks

    # -- public API -------------------------------------------------------

    def search(
        self,
        question: str,
        intent: str | list[str] | None = None,
        scope: str | None = None,
        seat: str | None = None,
        budget: str = "standard",
        min_sources: int | None = None,
        reformulate: bool = True,
        reformulator: Callable[[str], list[str]] | None = None,
        max_reformulations: int = 3,
    ) -> ResearchResult:
        """Route one question and return deduplicated, ranked records.

        If every source returns nothing, the query is reformulated and retried
        rather than reported as "no such literature". An empty result is far
        more often a badly-shaped query than an empty field, and the two are
        indistinguishable from the caller's side unless you actually try again
        differently.

        ``reformulator`` lets a caller supply lateral pivots -- an agent that
        rethinks the taxonomy rather than reshaping the string. Its suggestions
        are tried after the cheap structural transforms, because most dead ends
        are structural and a model call is the expensive option.
        """
        from .dedup import merge_records  # local import keeps module import cheap
        from .query import reformulations

        intents = resolve_intents(intent=intent, scope=scope, seat=seat)
        limits = budget_preset(budget)
        if min_sources is not None:
            limits["min_sources"] = min_sources

        def attempt(q: str) -> tuple[list[Record], list[str], list[str]]:
            records: list[Record] = []
            queried: list[str] = []
            fallbacks: list[str] = []
            for name in intents:
                r, qd, fb = self._run_chain(q, name, limits)
                records.extend(r)
                queried.extend(x for x in qd if x not in queried)
                fallbacks.extend(x for x in fb if x not in fallbacks)
            return records, queried, fallbacks

        all_records, all_queried, all_fallbacks = attempt(question)
        tried: list[dict[str, str]] = []

        if reformulate and not all_records:
            candidates = reformulations(keywordize(question))
            if reformulator:
                try:
                    candidates += [("agent", q) for q in reformulator(question) or []]
                except Exception:
                    pass  # a failing reformulator must not fail the search
            for strategy, candidate in candidates[:max_reformulations]:
                tried.append({"strategy": strategy, "query": candidate})
                records, queried, fallbacks = attempt(candidate)
                all_queried.extend(x for x in queried if x not in all_queried)
                all_fallbacks.extend(x for x in fallbacks if x not in all_fallbacks)
                if records:
                    all_records = records
                    break

        merged = merge_records(all_records)[: limits["max_records"]]
        self.stats["records_kept"] += len(merged)

        return ResearchResult(
            query=question,
            intent=intents[0],
            records=merged,
            sources_queried=all_queried,
            fallbacks_used=all_fallbacks,
            breaker_tripped=sorted(self._tripped),
            reformulations=tried,
            stats=dict(self.stats, intents=intents, budget=budget),
        )

    def savings_report(self) -> str:
        """What the budget and cache actually bought, in plain numbers."""
        s = self.stats
        fetched = s.get("records_fetched", 0)
        kept = s.get("records_kept", 0)
        return (
            f"live calls {s.get('live_calls', 0)} | "
            f"cache {s.get('cache_hits', 0)} hit / {s.get('cache_misses', 0)} miss | "
            f"records {fetched} fetched -> {kept} kept | "
            f"retries {s.get('retries', 0)} | "
            f"failures {s.get('adapter_failures', 0)} | "
            f"breaker {sorted(self._tripped) or 'clear'}"
        )
