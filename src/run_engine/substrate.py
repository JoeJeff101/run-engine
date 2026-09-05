"""The substrate: the state machine of the thing being acted on.

This is the one part of an instantiation that is genuinely about its subject
matter, and the only part replaced wholesale between domains. For a chemist it
is a compound's reachable states; for a manufacturer it is the cash conversion
cycle; for a clinician it is a patient population's exposure to a therapy.

The shape is always the same, and it is the shape that ports:

    rest --access--> convert --> intermediate --handling--> lock --fire--> locked
                                                                            |
                                                                    key ----+
                                                                     |
                                                                    rest

Nine stages. Eight of them describe getting somewhere; the ninth describes
getting back. The ninth is the one this module refuses to let you omit.

Why the key is mandatory
------------------------
A locked state you cannot deliberately undo is not an achievement, it is a
trap you built on purpose. In chemistry the question is "can I make a
reversible change permanent, and still reverse it when I choose?"; in finance
it is "can I unwind this position, and at what cost?". Both bottom out in the
same requirement, and the requirement is cheap to state and expensive to
discover you skipped. So a substrate without a key raises at load time rather
than failing review three gates later.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Ordered. The order is part of the contract: you cannot lock before you have
# an intermediate, and you cannot fire before you have locked.
STAGES: tuple[str, ...] = (
    "rest",          # default resting state; stable, unconverted, safe
    "access",        # reach the substrate without damaging it
    "convert",       # the conversion step, by non-proprietary means
    "intermediate",  # the valuable but perishable state
    "handling",      # survives ordinary, uncontrolled handling
    "lock",          # where reversible becomes durable
    "fire",          # the single irreversible commitment
    "locked",        # the achieved permanent state
    "key",           # the one deliberate mechanism that undoes it
)

STAGE_ROLES: dict[str, str] = {
    "rest": "Default rest state. Everything returns here — until it cannot.",
    "access": "Gain access without damaging the substrate or the channel.",
    "convert": "The conversion step, executed by means no counterparty can veto.",
    "intermediate": "Real value, decaying. Holds only for a bounded window.",
    "handling": "Must survive ordinary conditions, not laboratory ones.",
    "lock": "The step that makes the gain persist.",
    "fire": "One shot, large, unrecoverable. Deliberately not titratable.",
    "locked": "The achieved permanent state. This is what the board reviews.",
    "key": "The designed mechanism that undoes the locked state.",
}


class SubstrateError(ValueError):
    """Raised when a substrate definition is malformed or missing its key."""


@dataclass(frozen=True)
class Step:
    stage: str
    name: str
    description: str = ""
    window: str = ""  # how long this state holds unaided, where that is meaningful

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise SubstrateError(
                f"unknown stage {self.stage!r}. Expected one of: {', '.join(STAGES)}"
            )
        if not self.name.strip():
            raise SubstrateError(f"stage {self.stage!r}: needs a name in this domain's language")


@dataclass(frozen=True)
class Substrate:
    """A validated nine-stage state machine."""

    subject: str
    steps: tuple[Step, ...]

    def __post_init__(self) -> None:
        seen = [s.stage for s in self.steps]

        missing = [s for s in STAGES if s not in seen]
        if "key" in missing:
            raise SubstrateError(
                "substrate declares no 'key' stage. If there is no key, the lock is a "
                "trap: you would be designing a permanent state with no deliberate way "
                "out of it. Name the mechanism that reverses the locked state — returns "
                "and liquidation, a contract exit, an antidote, a rollback — or stop and "
                "design one before going further."
            )
        if missing:
            raise SubstrateError(
                f"substrate is missing stage(s): {', '.join(missing)}. All nine are "
                f"required; a stage you cannot name is a step you have not thought about."
            )

        dupes = {s for s in seen if seen.count(s) > 1}
        if dupes:
            raise SubstrateError(f"substrate declares duplicate stage(s): {', '.join(sorted(dupes))}")

        order = [STAGES.index(s) for s in seen]
        if order != sorted(order):
            out_of_place = [s for s, _ in sorted(zip(seen, order), key=lambda p: p[1])]
            raise SubstrateError(
                "substrate stages are out of order. Expected: "
                f"{' -> '.join(STAGES)}; got: {' -> '.join(seen)}. "
                f"(Reordered, they would read: {' -> '.join(out_of_place)}.)"
            )

    def step(self, stage: str) -> Step:
        found = next((s for s in self.steps if s.stage == stage), None)
        if found is None:  # unreachable while validation holds; kept as a real guard
            raise SubstrateError(f"substrate has no stage {stage!r}")
        return found

    @property
    def rest(self) -> Step:
        return self.step("rest")

    @property
    def lock(self) -> Step:
        return self.step("lock")

    @property
    def fire(self) -> Step:
        """The point of no return. Everything before it is reversible."""
        return self.step("fire")

    @property
    def locked(self) -> Step:
        return self.step("locked")

    @property
    def key(self) -> Step:
        return self.step("key")

    def digest(self) -> str:
        return "\n".join(
            f"  {s.stage:<12} {s.name}" + (f"  [{s.window}]" if s.window else "")
            for s in self.steps
        )

    @classmethod
    def load(cls, path: str | Path) -> "Substrate":
        p = Path(path)
        if not p.is_file():
            raise SubstrateError(f"substrate file not found: {p}")
        try:
            raw: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise SubstrateError(f"{p}: invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise SubstrateError(f"{p}: top level must be a mapping")

        entries = raw.get("steps") or []
        if not isinstance(entries, list):
            raise SubstrateError(f"{p}: 'steps' must be a list")

        steps = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise SubstrateError(f"{p}: each step must be a mapping")
            steps.append(Step(
                stage=str(entry.get("stage", "")),
                name=str(entry.get("name", "")),
                description=str(entry.get("description", "")),
                window=str(entry.get("window", "")),
            ))
        return cls(subject=str(raw.get("subject", "")), steps=tuple(steps))
