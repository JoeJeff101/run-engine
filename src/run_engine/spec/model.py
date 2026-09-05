"""The governed artifacts: brief, contract, spec, amendment, changelog.

These four files are the constitution of a run. Everything else in the engine
reads them and nothing else writes them, which is the point -- an agent that
can edit the rules it is judged against is not being judged.

Why these are data and not prose
--------------------------------
In the system this engine generalizes, the brief and the spec were markdown.
That reads well and enforces nothing: a brief can lose its second clause and
still look like a brief, and a prior can drift because someone edited a
sentence. Here the two-clause brief is a dataclass with two required fields,
so a brief missing its do-no-harm clause is a load error rather than a
document that quietly stopped constraining anything.

The master metric is the sharpest case. A performance target without a
survival constraint produces plans that win and kill the company, so
``MasterMetric`` refuses to construct without both halves.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml


class SpecError(ValueError):
    """Raised when a governed artifact is malformed or would be illegally changed."""


def _read_yaml(path: Path, what: str) -> dict[str, Any]:
    if not path.is_file():
        raise SpecError(f"{what} not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SpecError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise SpecError(f"{path}: top level must be a mapping")
    return raw


# ---------------------------------------------------------------------------
# 01 Brief -- the north star
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Brief:
    """One sentence naming the transformation you want and the thing you must
    not damage getting it. Two clauses, always.

    The second clause is the one people drop, and dropping it is not a
    stylistic choice -- it removes the only term that makes a plan's success
    distinguishable from its author's ruin.
    """

    gain: str
    do_no_harm: str
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.gain.strip():
            raise SpecError("brief: 'gain' is empty -- name the transformation you want")
        if not self.do_no_harm.strip():
            raise SpecError(
                "brief: 'do_no_harm' is empty. A brief with one clause is a wish. "
                "Name what must survive the attempt, or you do not yet know what "
                "you are risking."
            )

    def one_line(self) -> str:
        return f"{self.gain.rstrip('.')} — without {self.do_no_harm.lstrip('without ').rstrip('.')}."

    @classmethod
    def load(cls, path: str | Path) -> "Brief":
        raw = _read_yaml(Path(path), "brief")
        return cls(
            gain=str(raw.get("gain", "")),
            do_no_harm=str(raw.get("do_no_harm", "")),
            notes=str(raw.get("notes", "")),
        )


# ---------------------------------------------------------------------------
# Agent contract -- the rules of claim-making
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Contract:
    """What counts as evidence, what must be tagged a guess, what may never be
    asserted. Injected into every agent at grounding time, every run.

    Held as data rather than a prompt constant so that a pack can tighten its
    own discipline without editing the engine, and so the rules a given run was
    conducted under are recoverable from that run's archive.
    """

    rules: tuple[str, ...] = ()
    real_requires: str = ""
    never: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.rules:
            raise SpecError("contract: no rules declared")
        if not self.real_requires.strip():
            raise SpecError(
                "contract: 'real_requires' is empty -- state what a REAL row must "
                "carry, or the grade means nothing"
            )

    def as_prompt(self) -> str:
        lines = ["Evidence discipline (binding for this run):"]
        lines += [f"{i}. {r}" for i, r in enumerate(self.rules, 1)]
        lines.append(f"\nA claim may be graded REAL only if: {self.real_requires}")
        if self.never:
            lines.append("\nNever:")
            lines += [f"  - {n}" for n in self.never]
        return "\n".join(lines)

    @classmethod
    def load(cls, path: str | Path) -> "Contract":
        raw = _read_yaml(Path(path), "contract")
        return cls(
            rules=tuple(str(r) for r in raw.get("rules", []) or []),
            real_requires=str(raw.get("real_requires", "")),
            never=tuple(str(n) for n in raw.get("never", []) or []),
        )


# ---------------------------------------------------------------------------
# The master metric -- one scalar with two halves
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MasterMetric:
    """Every gate argument bottoms out here.

    ``performance`` is the thing you are trying to achieve; ``do_no_harm`` is
    the constraint that must hold while you achieve it. Both are required, for
    the same reason the brief needs two clauses -- and the requirement is
    enforced here rather than reviewed, because a review can be rushed.
    """

    performance: str
    do_no_harm: str

    def __post_init__(self) -> None:
        if not self.performance.strip():
            raise SpecError("master_metric: 'performance' half is missing")
        if not self.do_no_harm.strip():
            raise SpecError(
                "master_metric: 'do_no_harm' half is missing. A performance target "
                "without a survival constraint produces plans that win and kill the "
                "company."
            )

    def one_line(self) -> str:
        return f"{self.performance}, with {self.do_no_harm}"


# ---------------------------------------------------------------------------
# Gate declarations (the ladder itself lives in gates.py; this is the data)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateSpec:
    """A gate as the spec declares it, before any run has evaluated it.

    ``cost`` is the cost of *falsification* -- what it costs to find out this
    gate fails, not what it costs to pass it. The ladder is ordered by this
    number, and the ordering is the whole design.
    """

    id: str
    question: str
    cost: float
    prior_alpha: float = 1.0
    prior_beta: float = 1.0
    pass_condition: str = ""
    requirement_set: str = ""

    def __post_init__(self) -> None:
        if self.cost < 0:
            raise SpecError(f"gate {self.id}: cost of falsification cannot be negative")
        if self.prior_alpha <= 0 or self.prior_beta <= 0:
            raise SpecError(f"gate {self.id}: Beta prior parameters must be positive")
        if not self.pass_condition.strip():
            raise SpecError(
                f"gate {self.id}: no written pass condition. A gate whose pass "
                f"condition is decided after the result is not a gate."
            )


@dataclass(frozen=True)
class RequirementSet:
    """A named set of requirements, with its headline risk named out loud.

    Burying the worst risk among the others is how it stops being discussed.
    """

    name: str
    requirements: tuple[str, ...]
    headline_risk: str = ""


# ---------------------------------------------------------------------------
# Amendments and the changelog
# ---------------------------------------------------------------------------

PENDING, APPROVED, REJECTED, RATIFIED = "pending", "approved", "rejected", "ratified"


@dataclass
class Amendment:
    """A proposed change to the spec, queued rather than applied.

    Changes accumulate here instead of being slipped in between meetings. The
    status ladder is pending -> approved (the vote, key one) -> ratified (the
    independent check, key two). Only ratification touches the spec.
    """

    id: str
    proposal: str
    target: str  # dotted path into the spec, e.g. "gates.G0.prior_alpha"
    value: Any
    proposed_by: str = ""
    status: str = PENDING
    votes: dict[str, str] = field(default_factory=dict)
    ratified_by: str = ""

    def tally(self) -> tuple[int, int]:
        approve = sum(1 for v in self.votes.values() if v.upper().startswith("APPROVE"))
        reject = sum(1 for v in self.votes.values() if v.upper().startswith("REJECT"))
        return approve, reject

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "proposal": self.proposal, "target": self.target,
            "value": self.value, "proposed_by": self.proposed_by,
            "status": self.status, "votes": dict(self.votes),
            "ratified_by": self.ratified_by,
        }


@dataclass(frozen=True)
class ChangelogEntry:
    version: int
    amendment_id: str
    summary: str
    diff: str
    approved_by: str
    ratified_by: str
    at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version, "amendment_id": self.amendment_id,
            "summary": self.summary, "diff": self.diff,
            "approved_by": self.approved_by, "ratified_by": self.ratified_by, "at": self.at,
        }


# ---------------------------------------------------------------------------
# 06 System Spec -- the constitution
# ---------------------------------------------------------------------------


@dataclass
class Spec:
    """The technical constitution: the current best description of the system.

    Changed only by ratified amendment, never by edit. ``version`` is not
    decoration -- v10 means it already survived nine revisions, and the
    changelog says what each one was and who agreed to it.
    """

    version: int
    master_metric: MasterMetric
    gates: tuple[GateSpec, ...]
    requirement_sets: tuple[RequirementSet, ...] = ()
    market_prior: tuple[float, float] = (1.0, 1.0)
    core_disciplines: tuple[str, ...] = ()
    amendments: list[Amendment] = field(default_factory=list)
    changelog: list[ChangelogEntry] = field(default_factory=list)
    path: Path | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise SpecError("spec: version must be a positive integer")
        if not self.gates:
            raise SpecError("spec: no gates declared -- there is nothing to falsify")
        ids = [g.id for g in self.gates]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise SpecError(f"spec: duplicate gate id(s): {', '.join(sorted(dupes))}")
        a, b = self.market_prior
        if a <= 0 or b <= 0:
            raise SpecError("spec: market_prior parameters must be positive")

    def gate(self, gate_id: str) -> GateSpec | None:
        return next((g for g in self.gates if g.id == gate_id), None)

    def pending(self) -> list[Amendment]:
        return [a for a in self.amendments if a.status in (PENDING, APPROVED)]

    def digest(self) -> str:
        """The one-paragraph form injected into every agent at grounding."""
        lines = [
            f"System Spec v{self.version}",
            f"Master metric: {self.master_metric.one_line()}",
            "Gates (cheapest falsification first):",
        ]
        for g in sorted(self.gates, key=lambda g: g.cost):
            lines.append(f"  {g.id} (cost {g.cost:,.0f}): {g.question}")
        for rs in self.requirement_sets:
            head = f" — headline risk: {rs.headline_risk}" if rs.headline_risk else ""
            lines.append(f"Requirements [{rs.name}]: {len(rs.requirements)} items{head}")
        if self.pending():
            lines.append(f"Pending amendments: {', '.join(a.id for a in self.pending())}")
        return "\n".join(lines)

    # -- serialisation ------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Spec":
        p = Path(path)
        raw = _read_yaml(p, "spec")

        mm = raw.get("master_metric") or {}
        if not isinstance(mm, dict):
            raise SpecError("spec: master_metric must be a mapping with two halves")
        metric = MasterMetric(
            performance=str(mm.get("performance", "")),
            do_no_harm=str(mm.get("do_no_harm", "")),
        )

        gates = []
        for entry in raw.get("gates") or []:
            if not isinstance(entry, dict) or "id" not in entry:
                raise SpecError("spec: each gate needs at least an id")
            prior = entry.get("prior") or {}
            gates.append(GateSpec(
                id=str(entry["id"]),
                question=str(entry.get("question", "")),
                cost=float(entry.get("cost", 0)),
                prior_alpha=float(prior.get("alpha", 1.0)),
                prior_beta=float(prior.get("beta", 1.0)),
                pass_condition=str(entry.get("pass_condition", "")),
                requirement_set=str(entry.get("requirement_set", "")),
            ))

        req_sets = tuple(
            RequirementSet(
                name=str(rs.get("name", "")),
                requirements=tuple(str(r) for r in rs.get("requirements", []) or []),
                headline_risk=str(rs.get("headline_risk", "")),
            )
            for rs in raw.get("requirement_sets") or []
        )

        mp = raw.get("market_prior") or {}
        amendments = [
            Amendment(
                id=str(a["id"]), proposal=str(a.get("proposal", "")),
                target=str(a.get("target", "")), value=a.get("value"),
                proposed_by=str(a.get("proposed_by", "")),
                status=str(a.get("status", PENDING)),
                votes=dict(a.get("votes") or {}),
                ratified_by=str(a.get("ratified_by", "")),
            )
            for a in raw.get("amendments") or []
        ]
        changelog = [
            ChangelogEntry(
                version=int(c.get("version", 0)), amendment_id=str(c.get("amendment_id", "")),
                summary=str(c.get("summary", "")), diff=str(c.get("diff", "")),
                approved_by=str(c.get("approved_by", "")), ratified_by=str(c.get("ratified_by", "")),
                at=str(c.get("at", "")),
            )
            for c in raw.get("changelog") or []
        ]

        return cls(
            version=int(raw.get("version", 0)),
            master_metric=metric,
            gates=tuple(gates),
            requirement_sets=req_sets,
            market_prior=(float(mp.get("alpha", 1.0)), float(mp.get("beta", 1.0))),
            core_disciplines=tuple(str(d) for d in raw.get("core_disciplines", []) or []),
            amendments=amendments,
            changelog=changelog,
            path=p,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "master_metric": {
                "performance": self.master_metric.performance,
                "do_no_harm": self.master_metric.do_no_harm,
            },
            "core_disciplines": list(self.core_disciplines),
            "market_prior": {"alpha": self.market_prior[0], "beta": self.market_prior[1]},
            "requirement_sets": [
                {"name": rs.name, "requirements": list(rs.requirements),
                 "headline_risk": rs.headline_risk}
                for rs in self.requirement_sets
            ],
            "gates": [
                {"id": g.id, "question": g.question, "cost": g.cost,
                 "pass_condition": g.pass_condition, "requirement_set": g.requirement_set,
                 "prior": {"alpha": g.prior_alpha, "beta": g.prior_beta}}
                for g in self.gates
            ],
            "amendments": [a.to_dict() for a in self.amendments],
            "changelog": [c.to_dict() for c in self.changelog],
        }

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise SpecError("spec: no path to save to")
        target.write_text(
            yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return target

    def with_version(self, version: int) -> "Spec":
        return replace(self, version=version)
