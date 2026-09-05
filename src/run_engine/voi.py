"""Value of information: which experiment to fund next, and why that one.

    VoI per dollar = ( Var(term) - E[Var(term | experiment)] ) x stake / cost

The node this implements is the one that decides what you spend money on next
month, and it is the reason the gate ladder is ordered the way it is. That
ordering is not a convention imposed on the arithmetic -- it falls out of it. A
cheap demand test collapses far more variance per dollar than an expensive
pilot, so it ranks first without anyone deciding that it should.

The closed form
---------------
For a Beta(a, b) belief updated by n Bernoulli trials, the expected posterior
variance has an exact solution. Averaging over the prior predictive:

    E[Var(t | X)] = Var(t) * (a + b) / (a + b + n)

so the expected variance *reduction* is ``Var(t) * n / (a + b + n)``. No
simulation, no sampling, and therefore no seed to disagree about. Two things
follow immediately and are worth stating because they are the whole intuition:
the first observations are worth far more than the last, and an experiment
against a belief you already hold firmly (large a + b) buys almost nothing.

Where the source system used ``(1 - gate_score)`` as a stand-in for variance
and hand-entered "informativeness" priors, this uses the actual posterior
variance of the actual belief. That coupling is deliberate: the same Beta that
produces the probability produces the funding order, so they cannot drift
apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .probability import Beta


class VoIError(ValueError):
    """Raised when an experiment queue is malformed."""


@dataclass(frozen=True)
class Experiment:
    """A candidate experiment, with an SOP number so someone else can run it."""

    id: str
    label: str
    term: str          # which gate or term this would inform
    cost: float        # what it costs to run, in whatever unit the pack uses
    observations: int  # how many trials it is expected to yield
    stake: float = 1.0  # how much rides on this term relative to the others
    note: str = ""

    def __post_init__(self) -> None:
        if self.cost <= 0:
            raise VoIError(f"{self.id}: cost must be positive — a free experiment ranks infinitely")
        if self.observations <= 0:
            raise VoIError(f"{self.id}: an experiment that yields no observations is not one")
        if self.stake <= 0:
            raise VoIError(f"{self.id}: stake must be positive")


@dataclass(frozen=True)
class Ranked:
    experiment: Experiment
    variance_before: float
    variance_after: float
    value_per_cost: float

    @property
    def reduction(self) -> float:
        return self.variance_before - self.variance_after

    def line(self) -> str:
        return (f"{self.experiment.id} — {self.experiment.label} "
                f"(cost {self.experiment.cost:,.0f}, informs {self.experiment.term}): "
                f"variance {self.variance_before:.5f} → {self.variance_after:.5f}, "
                f"VoI/cost {self.value_per_cost:.3e}")


def expected_posterior_variance(prior: Beta, observations: int) -> float:
    """E[Var(theta | X)] for a Beta prior and n Bernoulli trials. Closed form."""
    if observations <= 0:
        return prior.variance
    n = float(observations)
    total = prior.alpha + prior.beta
    return prior.variance * total / (total + n)


def rank(
    experiments: Sequence[Experiment],
    posteriors: Mapping[str, Beta],
) -> list[Ranked]:
    """Order the queue by value of information per unit cost, highest first.

    An experiment aimed at a term nobody holds a belief about is skipped rather
    than guessed at -- a silent default prior here would quietly invent the
    ranking this function exists to compute.
    """
    ranked: list[Ranked] = []
    for exp in experiments:
        prior = posteriors.get(exp.term)
        if prior is None:
            continue
        before = prior.variance
        after = expected_posterior_variance(prior, exp.observations)
        ranked.append(Ranked(
            experiment=exp,
            variance_before=before,
            variance_after=after,
            value_per_cost=(before - after) * exp.stake / exp.cost,
        ))
    return sorted(ranked, key=lambda r: (-r.value_per_cost, r.experiment.id))


def top_pick(ranked: Sequence[Ranked]) -> Ranked | None:
    return ranked[0] if ranked else None


def to_markdown(ranked: Sequence[Ranked], *, unfunded: Sequence[str] = ()) -> str:
    lines = ["# Value of information", "",
             "Ranked by variance reduction per unit cost. Run the top one first.", "",
             "| Rank | Experiment | Informs | Cost | Var before | Var after | VoI/cost |",
             "|---:|---|---|---:|---:|---:|---:|"]
    for i, r in enumerate(ranked, 1):
        lines.append(
            f"| {i} | {r.experiment.id} {r.experiment.label} | {r.experiment.term} | "
            f"{r.experiment.cost:,.0f} | {r.variance_before:.5f} | {r.variance_after:.5f} | "
            f"{r.value_per_cost:.3e} |")
    lines.append("")
    pick = top_pick(ranked)
    if pick:
        lines += [
            f"**Fund next: {pick.experiment.id} — {pick.experiment.label}.** "
            f"It collapses more uncertainty per unit spent than anything else queued"
            + (f". {pick.experiment.note}" if pick.experiment.note else "."),
            "",
        ]
    if unfunded:
        lines += [
            f"_Not ranked (no belief declared for: {', '.join(sorted(set(unfunded)))}). "
            f"An experiment informing a term with no prior cannot be scored — declare "
            f"the prior in the spec first._", "",
        ]
    return "\n".join(lines)


def load_experiments(path: str | Path) -> list[Experiment]:
    p = Path(path)
    if not p.is_file():
        raise VoIError(f"experiment queue not found: {p}")
    try:
        raw: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise VoIError(f"{p}: invalid YAML: {exc}") from exc
    entries = raw.get("experiments") if isinstance(raw, dict) else raw
    out: list[Experiment] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            raise VoIError(f"{p}: each experiment must be a mapping")
        out.append(Experiment(
            id=str(entry.get("id", "")), label=str(entry.get("label", "")),
            term=str(entry.get("term", "")), cost=float(entry.get("cost", 0) or 0),
            observations=int(entry.get("observations", 0) or 0),
            stake=float(entry.get("stake", 1.0)), note=str(entry.get("note", "")),
        ))
    return out


def unranked_terms(experiments: Iterable[Experiment], posteriors: Mapping[str, Beta]) -> list[str]:
    return [e.term for e in experiments if e.term not in posteriors]
