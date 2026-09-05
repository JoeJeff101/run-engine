"""Shared connector plumbing.

Every connector is a plain callable with the same signature, which is what lets
the router treat nine very different APIs as interchangeable and lets tests swap
all of them for fakes in one line.

Two conventions matter and are enforced here rather than left to each connector:

**A key-gated source with no key skips; it does not fail.** Returning an empty
list for "not configured" keeps the pipeline usable by someone who cloned this
five minutes ago with zero credentials. The chain simply falls through to the
next source.

**Transport failure raises; an empty answer returns.** ``SourceUnavailable`` is
raised when a source could not answer -- timeout, 5xx, rate limit, malformed
payload. An empty list means the source *did* answer and the answer was
"nothing". Collapsing these two would let a timed-out patent database read as
"no prior art found", which is the most expensive mistake this system could
make, so the distinction is load-bearing all the way up to the ledger.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from ..models import Record
from ..router import SourceUnavailable
from ..throttle import GOVERNOR

USER_AGENT_BASE = "evidence-pipeline/0.1 (+https://github.com/JoeJeff101/evidence-pipeline)"
DEFAULT_TIMEOUT = 30

# Status codes worth retrying. 429 is included because the router's backoff is
# the right response to a rate limit -- but note that hitting 429 at all means
# the throttle interval for that provider is too aggressive and should be fixed
# at the source rather than papered over with retries.
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


def contact_email() -> str | None:
    """Contact address for provider 'polite pools'.

    Read from the environment, never hardcoded. Several providers give faster
    and more reliable service to requests that identify a contact address, and
    several ask for one in their terms. Baking a personal address into source
    is both a privacy leak and wrong for anyone else running this.
    """
    email = (os.environ.get("CONTACT_EMAIL") or "").strip()
    return email or None


def user_agent() -> str:
    email = contact_email()
    return f"{USER_AGENT_BASE} (mailto:{email})" if email else USER_AGENT_BASE


def http_get(
    provider: str,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Any:
    """Throttled GET returning parsed JSON, or raising SourceUnavailable."""
    GOVERNOR.wait(provider)
    merged = {"User-Agent": user_agent(), "Accept": "application/json"}
    if headers:
        merged.update(headers)
    try:
        resp = requests.get(url, params=params, headers=merged, timeout=timeout)
    except requests.RequestException as exc:
        raise SourceUnavailable(f"{provider}: transport error: {exc}") from exc

    if resp.status_code in RETRYABLE:
        raise SourceUnavailable(f"{provider}: HTTP {resp.status_code}")
    if resp.status_code in (401, 403):
        # Credential problem. Not retryable and not this run's business to fix;
        # treat as "source unavailable to me" and let the chain move on.
        raise SourceUnavailable(f"{provider}: HTTP {resp.status_code} (auth)")
    if resp.status_code == 404:
        return None
    if resp.status_code in (400, 410, 422):
        # Permanent. A 400 usually means this connector is sending something the
        # API no longer accepts -- a renamed field, a retired parameter. Retrying
        # cannot help, and the error body names the problem, so surface it.
        detail = resp.text[:200].replace("\n", " ")
        raise SourceUnavailable(f"{provider}: HTTP {resp.status_code}: {detail}", retryable=False)
    if resp.status_code >= 400:
        raise SourceUnavailable(f"{provider}: HTTP {resp.status_code}")

    try:
        return resp.json()
    except ValueError as exc:
        raise SourceUnavailable(f"{provider}: malformed JSON") from exc


def clean(text: str | None, limit: int = 4000) -> str | None:
    if not text:
        return None
    flat = " ".join(str(text).split())
    return flat[:limit] or None


def make_record(source: str, title: str | None, **kwargs: Any) -> Record | None:
    """Build a Record, dropping anything without a usable title."""
    title = clean(title, 500)
    if not title:
        return None
    return Record(title=title, source=source, **kwargs)
