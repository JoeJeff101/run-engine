"""The gate ladder: cheapest falsification first, and one place where a run advances.

Two ideas do all the work here.

**Order by the cost of falsification, not by the cost of success.** The
question is "what does it cost to find out this is wrong?", and the cheapest
answer runs first. A landing page before a pilot run before a stress model
before a liquidation analysis; a bench test before a field trial. Reordering
the ladder to get the exciting gate done first destroys the economics of the
whole thing, so the order is computed from declared cost rather than declared
by the author.

**A failed gate defunds everything downstream.** This is the rule that saves
the most money and is broken the most often, because the day a gate goes red
is exactly the day everyone has reasons why the tooling order should proceed
anyway. Failure is therefore a routing decision made in code: the ladder marks
every later gate ``defunded``, and sends the run back to lock design rather
than filing a note.

There is no partial credit. Three of four gates green produces no evidence,
because a ladder is a conjunction -- the last gate is the one that asks
whether failure is survivable, and a plan that never answered it has not
been shown to be safe to attempt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .spec.model import GateSpec, Spec

PASS, FAIL, PENDING, DEFUNDED = "pass", "fail", "pending", "defunded"
STATUSES = (PASS, FAIL, PENDING, DEFUNDED)


class GateError(ValueError):
    """Raised when a ladder is malformed or given an unknown status."""


@dataclass(frozen=True)
class GateOutcome:
    gate: GateSpec
    status: str
    evidence: str = ""
    note: str = ""

    @property
    def id(self) -> str:
        return self.gate.id

    def line(self) -> str:
        return f"{self.id} — {self.status.upper()} — {self.gate.question}"


@dataclass
class LadderOutcome:
    """What the ladder decided, and what the run is allowed to spend next."""

    outcomes: tuple[GateOutcome, ...]
    failed_at: str | None = None
    routed_to: str = ""
    defunded: tuple[str, ...] = ()

    @property
    def advanced(self) -> bool:
        """True only when every gate is green. A conjunction, not an average."""
        return all(o.status == PASS for o in self.outcomes)

    @property
    def blocked(self) -> bool:
        return self.failed_at is not None

    def status_of(self, gate_id: str) -> str:
        found = next((o for o in self.outcomes if o.id == gate_id), None)
        if found is None:
            raise GateError(f"no such gate in this ladder: {gate_id}")
        return found.status

    def funded(self) -> tuple[str, ...]:
        return tuple(o.id for o in self.outcomes if o.status != DEFUNDED)

    def to_markdown(self) -> str:
        lines = ["## Gate ladder", "", "Ordered by cost of falsification, cheapest first.", "",
                 "| Gate | Cost to falsify | Basis | Status | Question |",
                 "|---|---:|---|---|---|"]
        for o in self.outcomes:
            # The cost column is tagged EST because a cost of falsification is an
            # estimate until someone has actually paid it -- and an untagged figure
            # is exactly what the linter is there to catch, including in our own output.
            lines.append(
                f"| {o.id} | {o.gate.cost:,.0f} | EST | {o.status.upper()} | "
                f"{o.gate.question} |")
        lines.append("")
        if self.blocked:
            lines += [
                f"**{self.failed_at} failed.** Downstream work is defunded: "
                f"{', '.join(self.defunded) if self.defunded else 'none remaining'}. "
                f"The run routes back to {self.routed_to or 'lock design'} to rework the "
                f"mechanism. Do not fund tooling, inventory, hiring or marketing against "
                f"this plan until it re-enters the ladder and clears.", "",
            ]
        elif self.advanced:
            lines += ["**Every gate cleared.** Evidence may be written to the ledger.", ""]
        else:
            lines += ["**Not yet clear.** Gates remain pending; no evidence is written "
                      "until every gate is green. There is no partial credit.", ""]
        return "\n".join(lines)


def _order(gates: Sequence[GateSpec]) -> list[GateSpec]:
    """Cheapest falsification first, subject to what each gate needs to exist first.

    Cost alone is not quite the rule, and pretending it is produces a ladder
    that cannot be walked. A stress matrix over real input costs is nearly free
    to *run* -- so pure cost ordering puts it first -- but it has nothing real
    to stress until a pilot has produced actual invoices. Likewise a
    reversibility analysis is mostly reading contracts you do not have yet.

    So the ordering is: among the gates whose dependencies are already placed,
    always take the cheapest. Dependencies constrain what may be considered;
    cost decides among what is left. With no dependencies declared -- the common
    case -- this reduces exactly to cost order.
    """
    by_id = {g.id: g for g in gates}
    for g in gates:
        unknown = [d for d in g.depends_on if d not in by_id]
        if unknown:
            raise GateError(
                f"gate {g.id} depends on {', '.join(unknown)}, which is not on this ladder")

    placed: list[GateSpec] = []
    done: set[str] = set()
    remaining = list(gates)
    while remaining:
        ready = [g for g in remaining if all(d in done for d in g.depends_on)]
        if not ready:
            stuck = ", ".join(sorted(g.id for g in remaining))
            raise GateError(
                f"the gate dependencies form a cycle among: {stuck}. A ladder you "
                f"cannot start climbing is not a ladder.")
        nxt = min(ready, key=lambda g: (g.cost, g.id))
        placed.append(nxt)
        done.add(nxt.id)
        remaining.remove(nxt)
    return placed


@dataclass
class Ladder:
    """An ordered gate ladder built from a spec."""

    gates: tuple[GateSpec, ...]
    lock_task: str = "T03"  # where a failure routes back to: mechanism design

    def __post_init__(self) -> None:
        if not self.gates:
            raise GateError("a ladder with no gates cannot falsify anything")
        # Sorting here rather than trusting declaration order is deliberate:
        # the ordering is a property of the costs, not of the author's habits.
        object.__setattr__(self, "gates", tuple(_order(self.gates)))

    @classmethod
    def from_spec(cls, spec: Spec, *, lock_task: str = "T03") -> "Ladder":
        return cls(gates=spec.gates, lock_task=lock_task)

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(g.id for g in self.gates)

    @property
    def cheapest(self) -> GateSpec:
        return self.gates[0]

    def evaluate(
        self,
        results: Mapping[str, str],
        *,
        evidence: Mapping[str, str] | None = None,
    ) -> LadderOutcome:
        """Walk the ladder in cost order and apply the defunding rule.

        ``results`` maps gate id -> pass/fail/pending. Anything not mentioned is
        pending: a gate nobody ran has not passed, and silence is not a result.
        """
        evidence = evidence or {}
        for gate_id, status in results.items():
            if status not in STATUSES:
                raise GateError(
                    f"gate {gate_id}: unknown status {status!r} (expected one of {STATUSES})")

        outcomes: list[GateOutcome] = []
        failed_at: str | None = None
        defunded: list[str] = []

        for gate in self.gates:
            if failed_at is not None:
                # Everything after a red gate stops being funded the same day.
                outcomes.append(GateOutcome(
                    gate, DEFUNDED, note=f"defunded by the failure of {failed_at}"))
                defunded.append(gate.id)
                continue

            status = results.get(gate.id, PENDING)
            outcomes.append(GateOutcome(gate, status, evidence=evidence.get(gate.id, "")))
            if status == FAIL:
                failed_at = gate.id

        return LadderOutcome(
            outcomes=tuple(outcomes),
            failed_at=failed_at,
            routed_to=self.lock_task if failed_at else "",
            defunded=tuple(defunded),
        )


# ---------------------------------------------------------------------------
# The master metric as a score
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricScore:
    """Where every argument bottoms out.

    Both halves are reported separately and then combined, because a plan that
    scores well on performance while violating its do-no-harm clause has not
    scored well -- it has found a way to lose that the average was hiding.
    """

    performance: float
    do_no_harm_held: bool
    lint_blocking: int
    detail: dict[str, float] = field(default_factory=dict)

    @property
    def value(self) -> float:
        """A single scalar in [0, 1]. Zero if the do-no-harm half is violated."""
        if not self.do_no_harm_held:
            return 0.0
        penalty = min(0.5, 0.05 * self.lint_blocking)
        return max(0.0, min(1.0, self.performance - penalty))

    def line(self) -> str:
        if not self.do_no_harm_held:
            return "0.00 — the do-no-harm half is violated; the performance half is moot"
        note = f" (−{0.05 * self.lint_blocking:.2f} for {self.lint_blocking} blocking lint findings)" \
            if self.lint_blocking else ""
        return f"{self.value:.2f}{note}"


def score_master_metric(
    ladder: LadderOutcome,
    *,
    do_no_harm_held: bool,
    lint_blocking: int = 0,
    requirements_met: Sequence[bool] = (),
) -> MetricScore:
    """Collapse gates, requirements and rule checks into one number.

    Requirement sets do not get to be argued at individually -- you cannot win
    by citing one requirement, you have to move the metric. Rule failures from
    the finalize step feed in as a fourth requirement set, so a dossier with
    unsourced figures cannot score well however good its argument is.
    """
    gate_share = (
        sum(1 for o in ladder.outcomes if o.status == PASS) / len(ladder.outcomes)
        if ladder.outcomes else 0.0
    )
    req_share = (sum(1 for r in requirements_met if r) / len(requirements_met)
                 if requirements_met else 1.0)
    performance = 0.7 * gate_share + 0.3 * req_share
    return MetricScore(
        performance=performance,
        do_no_harm_held=do_no_harm_held,
        lint_blocking=lint_blocking,
        detail={"gates": gate_share, "requirements": req_share},
    )


def requirement_status(spec: Spec, met: Iterable[str]) -> list[tuple[str, str, bool]]:
    """Flatten the spec's requirement sets into (set, requirement, met) triples."""
    met_set = set(met)
    out: list[tuple[str, str, bool]] = []
    for rs in spec.requirement_sets:
        for req in rs.requirements:
            out.append((rs.name, req, req in met_set))
    return out
