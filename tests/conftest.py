"""Shared fixtures.

The autouse fixture below is the most important line in the test suite: it
replaces every source adapter with a deterministic fake for *every* test.

Total replacement is deliberate. If only the adapters a test expects to use were
faked, then a routing bug -- a fallback firing when it should not, a breaker
failing to trip -- would reach a live API. The test would still pass, slowly,
while making unattributed network calls. Faking the whole registry means a
routing bug shows up as a test failure instead of as traffic.
"""

from __future__ import annotations

import pytest

from run_engine.retrieval.router import Router
from run_engine.testing import fake_adapters


@pytest.fixture
def calls() -> dict[str, int]:
    """Per-source invocation counter, so tests can assert what was *not* called."""
    return {}


@pytest.fixture
def router(calls: dict[str, int]) -> Router:
    return Router(adapters=fake_adapters(calls), sleep=lambda _: None)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hard block: any real HTTP call from a connector fails loudly."""

    def _forbidden(*args, **kwargs):  # pragma: no cover - only runs on a bug
        raise AssertionError(
            "a test attempted a real network call; adapters should be faked"
        )

    import run_engine.retrieval.sources.base as base

    monkeypatch.setattr(base.requests, "get", _forbidden)


@pytest.fixture(autouse=True)
def _no_throttle_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the governor's semantics without paying its wall-clock cost."""
    import run_engine.retrieval.throttle as throttle

    monkeypatch.setattr(throttle.time, "sleep", lambda _: None)
