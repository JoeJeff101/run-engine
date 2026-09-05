"""Routing, fallback, circuit breaker, budgets, and cache."""

from __future__ import annotations

import pytest

from run_engine.evidence.models import Record, ResearchResult
from run_engine.retrieval.profiles import INTENTS, SEAT_PROFILES, budget, resolve_intents
from run_engine.retrieval.router import Router
from run_engine.testing import fake_adapters, fake_source

# ---------------------------------------------------------------------------
# Intent resolution precedence
# ---------------------------------------------------------------------------


def test_explicit_intent_beats_scope_and_seat():
    assert resolve_intents(intent="patent_prior_art", scope="literature", seat="lead") == [
        "patent_prior_art"
    ]


def test_scope_beats_seat():
    assert resolve_intents(scope="patents", seat="lead_investigator") == ["patent_prior_art"]


def test_seat_profile_used_when_nothing_more_specific():
    assert resolve_intents(seat="prior_art_analyst")[0] == "patent_prior_art"


def test_seat_alias_resolves():
    assert resolve_intents(seat="prior_art") == resolve_intents(seat="prior_art_analyst")


def test_unknown_seat_falls_back_to_default():
    assert resolve_intents(seat="chief_vibes_officer") == ["discovery"]


def test_invalid_intent_does_not_raise_and_falls_through():
    # A typo should degrade, not abort a long run.
    assert resolve_intents(intent="nonsense") == ["discovery"]
    assert resolve_intents(intent="nonsense", scope="patents") == ["patent_prior_art"]


def test_every_seat_profile_maps_to_known_intents():
    for seat, intents in SEAT_PROFILES.items():
        assert intents, f"{seat} has no intents"
        for name in intents:
            assert name in INTENTS, f"{seat} references unknown intent {name}"


def test_every_intent_chain_references_real_sources():
    known = set(fake_adapters().keys())
    for name, spec in INTENTS.items():
        assert spec["primary_db"] in spec["chain"], f"{name}: primary_db not in its own chain"
        for source in spec["chain"]:
            assert source in known, f"{name}: chain references unknown source {source}"


# ---------------------------------------------------------------------------
# Fallback and circuit breaker
# ---------------------------------------------------------------------------


def test_fallback_recorded_when_primary_is_empty(calls):
    adapters = fake_adapters(calls)
    adapters["openalex"] = fake_source("openalex", empty=True, calls=calls)
    router = Router(adapters=adapters, sleep=lambda _: None)

    result = router.search("a question", intent="discovery", budget="standard")

    assert "semantic_scholar" in result.fallbacks_used
    assert result.records


def test_healthy_primary_means_fallback_is_never_invoked(calls):
    """With lean budget (thin_hits=1) a productive primary ends the walk.

    Asserting the fallback was never *called* -- not merely that its records
    were absent -- is the point. An early stop that still pays for the call
    saves nothing.
    """
    router = Router(adapters=fake_adapters(calls), sleep=lambda _: None)
    result = router.search("a question", intent="discovery", budget="lean")

    assert calls.get("openalex") == 1
    assert calls.get("semantic_scholar") is None
    assert not result.fallbacks_used


def test_min_sources_prevents_early_stop_collapsing_a_breadth_sweep(calls):
    router = Router(adapters=fake_adapters(calls), sleep=lambda _: None)
    router.search("a question", intent="breadth_sweep", budget="deep")

    # deep sets min_sources=3, so a productive first source must not end it.
    assert sum(1 for v in calls.values() if v) >= 3


def test_breaker_trips_after_two_consecutive_failures(calls):
    """Two failed searches trip the source; the third must not call it at all.

    The trip happens *during* the second search -- the source is queried, fails,
    and only then crosses the threshold. So the observable effect is on the
    third search onward.
    """
    adapters = fake_adapters(calls)
    adapters["openalex"] = fake_source("openalex", fail=True, calls=calls)
    router = Router(adapters=adapters, sleep=lambda _: None)

    router.search("q1", intent="discovery", budget="standard")
    second = router.search("q2", intent="discovery", budget="standard")
    assert "openalex" in second.breaker_tripped

    after_trip = calls["openalex"]
    third = router.search("q3", intent="discovery", budget="standard")

    assert calls["openalex"] == after_trip, "a tripped source must not be called again"
    assert "openalex" not in third.sources_queried
    assert third.records, "the chain still answers via its fallbacks"


def test_breaker_resets_between_runs(calls):
    adapters = fake_adapters(calls)
    adapters["openalex"] = fake_source("openalex", fail=True, calls=calls)
    router = Router(adapters=adapters, sleep=lambda _: None)

    router.search("q", intent="discovery")
    assert router.search("q2", intent="discovery").breaker_tripped == ["openalex"]

    router.reset_run()
    assert router.search("q3", intent="discovery").breaker_tripped == []


def test_retries_are_attempted_before_a_failure_counts(calls):
    adapters = {"openalex": fake_source("openalex", fail=True, calls=calls)}
    router = Router(adapters=adapters, max_retries=2, sleep=lambda _: None)
    router.search("q", intent="discovery")
    # 1 initial attempt + 2 retries
    assert calls["openalex"] == 3


def test_permanent_failures_are_not_retried(calls):
    """A 400-class error fails identically on the second attempt.

    Retrying it wastes the run's time and hides how fast the source actually
    rejected the request.
    """
    from run_engine.retrieval.router import SourceUnavailable

    def permanently_broken(query, **kwargs):
        calls["openalex"] = calls.get("openalex", 0) + 1
        raise SourceUnavailable("openalex: HTTP 400: bad field", retryable=False)

    router = Router(adapters={"openalex": permanently_broken}, max_retries=2, sleep=lambda _: None)
    router.search("q", intent="discovery")

    assert calls["openalex"] == 1, "a permanent failure must be attempted exactly once"


def test_permanent_failure_still_counts_toward_the_breaker(calls):
    from run_engine.retrieval.router import SourceUnavailable

    def permanently_broken(query, **kwargs):
        calls["openalex"] = calls.get("openalex", 0) + 1
        raise SourceUnavailable("openalex: HTTP 400", retryable=False)

    adapters = fake_adapters(calls)
    adapters["openalex"] = permanently_broken
    router = Router(adapters=adapters, sleep=lambda _: None)

    router.search("q1", intent="discovery")
    assert "openalex" in router.search("q2", intent="discovery").breaker_tripped


def test_total_failure_returns_empty_result_rather_than_raising():
    adapters = {key: fake_source(key, fail=True) for key in fake_adapters()}
    router = Router(adapters=adapters, sleep=lambda _: None)

    result = router.search("a question", intent="discovery")

    assert isinstance(result, ResearchResult)
    assert result.records == []


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


def test_budget_caps_max_records(router):
    result = router.search("a question", intent="breadth_sweep", budget="lean")
    assert len(result.records) <= budget("lean")["max_records"]


def test_unknown_budget_name_degrades_to_standard():
    assert budget("nonsense") == budget("standard")


def test_pack_respects_char_ceiling(router):
    result = router.search("a question", intent="breadth_sweep", budget="deep")
    packed = result.pack(pack_chars=500, excerpt_chars=100)
    assert len(packed) <= 600  # ceiling plus one partial chunk boundary


def test_best_text_prefers_tldr_then_excerpt_then_abstract():
    rec = Record(title="t", source="s", abstract="ABSTRACT", excerpt="EXCERPT", tldr="TLDR")
    assert rec.best_text(100) == "TLDR"
    rec.tldr = None
    assert rec.best_text(100) == "EXCERPT"
    rec.excerpt = None
    assert rec.best_text(100) == "ABSTRACT"
    rec.abstract = None
    assert rec.best_text(100) == ""


def test_best_text_truncates_to_limit():
    rec = Record(title="t", source="s", abstract="x" * 500)
    assert len(rec.best_text(120)) == 120


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_identical_query_is_served_from_cache(tmp_path, calls):
    router = Router(adapters=fake_adapters(calls), cache_dir=tmp_path, sleep=lambda _: None)

    router.search("thermal stability of interfaces", intent="discovery", budget="lean")
    first = dict(calls)

    router.search("thermal stability of interfaces", intent="discovery", budget="lean")

    assert calls == first, "second identical query must not call any adapter"
    assert router.stats["cache_hits"] >= 1


def test_differently_worded_question_hits_the_same_cache_entry(tmp_path, calls):
    """The cache key is the normalized query, not the raw string."""
    router = Router(adapters=fake_adapters(calls), cache_dir=tmp_path, sleep=lambda _: None)

    router.search("What is the thermal stability of interfaces?", intent="discovery", budget="lean")
    first = dict(calls)
    router.search("thermal stability of interfaces", intent="discovery", budget="lean")

    assert calls == first
    assert router.stats["cache_hits"] >= 1


def test_different_query_misses_cache(tmp_path, calls):
    router = Router(adapters=fake_adapters(calls), cache_dir=tmp_path, sleep=lambda _: None)
    router.search("membrane selectivity", intent="discovery", budget="lean")
    router.search("dendrite suppression", intent="discovery", budget="lean")
    assert calls["openalex"] == 2


def test_expired_cache_entry_is_a_miss(tmp_path, calls):
    router = Router(adapters=fake_adapters(calls), cache_dir=tmp_path, cache_ttl=0, sleep=lambda _: None)
    router.search("a question", intent="discovery", budget="lean")
    router.search("a question", intent="discovery", budget="lean")
    assert calls["openalex"] == 2


def test_corrupt_cache_entry_is_a_miss_not_a_crash(tmp_path, calls):
    router = Router(adapters=fake_adapters(calls), cache_dir=tmp_path, sleep=lambda _: None)
    router.search("a question", intent="discovery", budget="lean")
    for path in tmp_path.glob("*.json"):
        path.write_text("{ not json", encoding="utf-8")

    result = router.search("a question", intent="discovery", budget="lean")
    assert result.records


# ---------------------------------------------------------------------------
# Offline degradation
# ---------------------------------------------------------------------------


def test_connectors_degrade_when_the_network_raises(monkeypatch):
    """With the transport patched to raise, a search still returns a result."""
    import run_engine.retrieval.sources.base as base

    def _boom(*args, **kwargs):
        raise base.requests.RequestException("network down")

    monkeypatch.setattr(base.requests, "get", _boom)

    router = Router(sleep=lambda _: None)  # the REAL adapters
    result = router.search("a question", intent="discovery", budget="lean")

    assert isinstance(result, ResearchResult)
    assert result.records == []


def test_source_unavailable_is_distinct_from_an_empty_answer(calls):
    """The distinction that keeps 'the database timed out' from reading as
    'no prior art exists'."""
    failing = Router(adapters={"patentsview": fake_source("patentsview", fail=True, calls=calls)},
                     sleep=lambda _: None)
    empty = Router(adapters={"patentsview": fake_source("patentsview", empty=True)},
                   sleep=lambda _: None)

    unavailable = failing.search("q", intent="patent_prior_art")
    answered = empty.search("q", intent="patent_prior_art")

    assert unavailable.records == answered.records == []
    assert unavailable.stats["adapter_failures"] > 0
    assert answered.stats["adapter_failures"] == 0
