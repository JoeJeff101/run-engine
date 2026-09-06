"""P(success): a number you can audit, rather than a number you can quote.

The board outputs a verdict. To get a probability you add one thing -- a prior
on each gate -- and let the evidence machinery do the updating. Nothing else
about the engine changes.

The chain is a product, not an average, because the gates are ordered and
conditional:

    P = P(G0) x P(G1|G0) x P(G2|G0,G1) x P(G3|...) x P(market | all gates)

Three rules make the number honest
----------------------------------
1. **Only REAL rows move it.** EST rows inform debate, set priors and shape the
   plan; they cannot move the arithmetic. This is the freeze rule applied to
   numbers, and it is enforced by ignoring any row whose grade is not REAL --
   so adding an EST row leaves the result bit-identical, which is a testable
   claim rather than a promise.

2. **Report three figures, never one.** Posterior mean, an 80% credible
   interval, and the REAL fraction -- the share of the estimate resting on
   promoted evidence rather than on priors. A plan at 62% with a REAL fraction
   of 0.05 is not a 62% plan; it is a guess with a decimal point, and saying so
   is the entire job of that third number.

3. **No dependencies.** The incomplete beta function and its inverse are
   implemented here rather than pulled from SciPy, because a decision engine
   that cannot run without a numerical stack is one people will not run.

On the interval
---------------
The product of independent Beta variables has no closed form. Rather than
sample -- which would make runs non-reproducible, and reproducibility is a
property this engine sells -- the product's exact mean and variance are
computed analytically and matched to a Beta, whose quantiles give the interval.
It is an approximation, it is deterministic, and it is documented here rather
than hidden behind a random seed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

MARKET = "market"

# How a ledger row encodes an observation. Anything else is an informational
# REAL row: it belongs in the record, but it does not pretend to be a trial.
_PASS = re.compile(r"^\s*(pass|passed|success|cleared|yes|true)\b", re.I)
_FAIL = re.compile(r"^\s*(fail|failed|failure|blocked|no|false)\b", re.I)
_RATIO = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*$")


# ---------------------------------------------------------------------------
# Special functions, so the engine has no numerical dependency
# ---------------------------------------------------------------------------


def _log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _betacf(a: float, b: float, x: float, *, iterations: int = 300, eps: float = 1e-14) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, iterations + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b) -- the Beta CDF."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(a * math.log(x) + b * math.log1p(-x) - _log_beta(a, b))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        b * math.log1p(-x) + a * math.log(x) - _log_beta(a, b)) * _betacf(b, a, 1.0 - x) / b


def beta_quantile(a: float, b: float, p: float, *, tol: float = 1e-10) -> float:
    """Inverse Beta CDF by bisection. Slow and exact enough; called a few times a run."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if betainc(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2.0


# ---------------------------------------------------------------------------
# Beta
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Beta:
    alpha: float
    beta: float

    def __post_init__(self) -> None:
        if self.alpha <= 0 or self.beta <= 0:
            raise ValueError("Beta parameters must be positive")

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def variance(self) -> float:
        a, b = self.alpha, self.beta
        n = a + b
        return (a * b) / (n * n * (n + 1.0))

    def interval(self, mass: float = 0.8) -> tuple[float, float]:
        tail = (1.0 - mass) / 2.0
        return (beta_quantile(self.alpha, self.beta, tail),
                beta_quantile(self.alpha, self.beta, 1.0 - tail))

    def updated(self, successes: float, failures: float) -> "Beta":
        if successes < 0 or failures < 0:
            raise ValueError("observation counts cannot be negative")
        return Beta(self.alpha + successes, self.beta + failures)


def _match_beta(mean: float, variance: float) -> Beta:
    """Moment-match a Beta to a given mean and variance, clamped to be valid."""
    mean = min(max(mean, 1e-9), 1.0 - 1e-9)
    ceiling = mean * (1.0 - mean)
    variance = min(max(variance, 1e-12), ceiling * 0.999999)
    common = ceiling / variance - 1.0
    return Beta(max(mean * common, 1e-9), max((1.0 - mean) * common, 1e-9))


# ---------------------------------------------------------------------------
# Observations from ledger rows
# ---------------------------------------------------------------------------


def observation(value: str) -> tuple[float, float] | None:
    """Read (successes, failures) out of a row's value, or None if it is not a trial.

    Accepted forms, and nothing else:
        "pass" / "fail"      one trial, one outcome
        "50/900"             50 successes out of 900 attempts

    Anything else is an informational REAL row. It stays in the record and does
    not move the number, because "we obtained the tooling quote" is evidence of
    diligence, not evidence that the gate passes.
    """
    text = str(value or "")
    m = _RATIO.match(text)
    if m:
        s, n = float(m.group(1)), float(m.group(2))
        if n < s or n <= 0:
            return None
        return s, n - s
    if _PASS.match(text):
        return 1.0, 0.0
    if _FAIL.match(text):
        return 0.0, 1.0
    return None


def score(value: str, *, min_rate: float | None = None) -> tuple[float, float] | None:
    """An observation, with a gate's declared threshold applied when it has one.

    ``observation`` answers "what does this cell literally say?". This answers
    the question the ladder and the arithmetic both actually ask: "did the gate
    clear?" They differ only for ratios, and only when the gate declared a
    target -- at which point the ratio is a measured rate to be compared against
    that target, and the result is a verdict rather than a pile of trials.

    Without the threshold, ``25/1000`` against G0 reads as 25 successes in 1,000
    attempts and collapses the term to 0.027 -- so logging a demand test that
    passed exactly on its written condition made the plan look fifteen times
    worse. With it, the same row is one clean pass.
    """
    obs = observation(value)
    if obs is None or min_rate is None:
        return obs
    successes, failures = obs
    trials = successes + failures
    if trials <= 0:
        return None
    return (1.0, 0.0) if successes / trials >= min_rate else (0.0, 1.0)


def _rows_for(rows: Iterable, term: str) -> list:
    out = []
    for row in rows:
        topic = str(getattr(row, "topic", "") or "").strip()
        if topic.upper() == term.upper():
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# The estimate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TermEstimate:
    term: str
    posterior: Beta
    real_observations: float
    prior_mass: float

    @property
    def mean(self) -> float:
        return self.posterior.mean


@dataclass(frozen=True)
class Estimate:
    """What the engine reports. Three figures, never one."""

    mean: float
    lo: float
    hi: float
    real_fraction: float
    terms: tuple[TermEstimate, ...] = ()
    mass: float = 0.8

    def line(self) -> str:
        pct = f"{self.mean:.1%}"
        interval = f"{int(self.mass * 100)}% CrI {self.lo:.1%}–{self.hi:.1%}"
        return f"P(success) = {pct} ({interval}) · REAL fraction {self.real_fraction:.2f}"

    def caveat(self) -> str:
        """The sentence that has to appear next to a low-evidence number."""
        if self.real_fraction < 0.10:
            return (
                f"This estimate rests almost entirely on priors (REAL fraction "
                f"{self.real_fraction:.2f}). It is not a forecast — it is the board's "
                f"stated assumptions, arithmetically combined. Treat it as a statement "
                f"of what would have to be true, and fund the top-ranked experiment."
            )
        if self.real_fraction < 0.35:
            return (
                f"REAL fraction {self.real_fraction:.2f}: a minority of this estimate is "
                f"backed by promoted evidence. The interval is wide for a reason."
            )
        return f"REAL fraction {self.real_fraction:.2f} of this estimate rests on promoted evidence."

    def to_dict(self) -> dict[str, float | str]:
        return {"mean": self.mean, "lo": self.lo, "hi": self.hi,
                "real_fraction": self.real_fraction, "mass": self.mass}


def estimate(
    priors: Mapping[str, tuple[float, float]],
    rows: Sequence = (),
    *,
    mass: float = 0.8,
    thresholds: Mapping[str, float] | None = None,
) -> Estimate:
    """Combine per-term Beta posteriors into one auditable probability.

    ``priors`` maps a term name (a gate id, or ``"market"``) to its Beta prior.
    ``rows`` are ledger rows; only those graded REAL are consulted, and only
    those whose value parses as an observation change anything.

    ``thresholds`` maps a term to the pass rate its spec declares, for gates
    whose ratio rows are measured rates rather than samples of trials. A term
    with no threshold behaves exactly as it always has.
    """
    if not priors:
        raise ValueError("no priors: there is nothing to estimate")

    thresholds = thresholds or {}
    terms: list[TermEstimate] = []
    for term, (alpha, beta) in priors.items():
        prior = Beta(float(alpha), float(beta))
        successes = failures = 0.0
        for row in _rows_for(rows, term):
            # The single line that enforces "only REAL rows move the number".
            if str(getattr(row, "grade", "")).upper() != "REAL":
                continue
            obs = score(getattr(row, "value", ""), min_rate=thresholds.get(term))
            if obs is None:
                continue
            successes += obs[0]
            failures += obs[1]
        terms.append(TermEstimate(
            term=term,
            posterior=prior.updated(successes, failures),
            real_observations=successes + failures,
            prior_mass=prior.alpha + prior.beta,
        ))

    mean = 1.0
    second = 1.0
    for t in terms:
        mean *= t.posterior.mean
        second *= t.posterior.variance + t.posterior.mean ** 2
    variance = max(second - mean * mean, 1e-12)

    lo, hi = _match_beta(mean, variance).interval(mass)

    observed = sum(t.real_observations for t in terms)
    prior_mass = sum(t.prior_mass for t in terms)
    real_fraction = observed / (observed + prior_mass) if (observed + prior_mass) else 0.0

    return Estimate(mean=mean, lo=lo, hi=hi, real_fraction=real_fraction,
                    terms=tuple(terms), mass=mass)


def priors_from_spec(spec) -> dict[str, tuple[float, float]]:
    """Every gate's prior plus the market term, in ladder order."""
    priors: dict[str, tuple[float, float]] = {
        g.id: (g.prior_alpha, g.prior_beta) for g in sorted(spec.gates, key=lambda g: g.cost)
    }
    priors[MARKET] = tuple(spec.market_prior)  # type: ignore[assignment]
    return priors


def thresholds_from_spec(spec) -> dict[str, float]:
    """Declared pass rates, for the gates that measure one. Usually sparse."""
    return {g.id: g.min_rate for g in spec.gates if g.min_rate is not None}
