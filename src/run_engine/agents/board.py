"""Boards: the roster as configuration.

The most important structural claim in this repository is that **the board is
configuration and the orchestration is the product**. Seats, phases, charters,
and model tiers live in YAML. Nothing in the code below knows or cares what the
board is investigating.

That separation is what makes the system portable across domains. A board for
materials research and a board for a legal prior-art review differ only in the
YAML; the handoff mechanics, the budget enforcement, the verification pass, and
the evidence discipline are identical.

Validation runs at load time and is strict, because every failure mode it
catches is one that would otherwise surface as *silence*: a seat whose upstream
dependency was misspelled receives an empty context and produces confident,
ungrounded output. Nothing errors. You just get worse answers and no signal that
anything went wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_TIERS = {"heavy", "light", "research"}


class BoardError(ValueError):
    """Raised when a board definition is malformed."""


@dataclass
class Seat:
    key: str
    title: str
    phase: str
    charter: str
    tier: str = "light"
    temperature: float = 0.7
    context: list[str] = field(default_factory=list)
    retrieval: bool = True
    # The discipline this seat represents. Quorum is counted in disciplines
    # rather than heads, so a board of twelve people who all do the same job is
    # correctly treated as not quorate.
    discipline: str = ""
    # Seats this one is chartered to attack. Distinct from `context`: context is
    # material to build on, `challenges` is material to try to break. Pairing a
    # divergent high-temperature seat with a skeptical low-temperature one is
    # how a board avoids converging on its most confident member.
    challenges: list[str] = field(default_factory=list)

    def system_prompt(self, board_subject: str, rules: str) -> str:
        return (
            f"You are the {self.title} on a research board.\n"
            f"Subject under review: {board_subject}\n\n"
            f"Your charter:\n{self.charter}\n\n"
            f"{rules}"
        )


@dataclass
class Board:
    name: str
    subject: str
    phases: list[str]
    seats: list[Seat]
    rules: str = ""
    # The minimum disciplines that must be represented for a vote to count.
    # Fewer than about five and one specialty captures the room; many more and
    # nothing gets decided.
    core_disciplines: list[str] = field(default_factory=list)

    def seat(self, key: str) -> Seat | None:
        return next((s for s in self.seats if s.key == key), None)

    def disciplines(self) -> set[str]:
        return {s.discipline for s in self.seats if s.discipline}

    def missing_disciplines(self) -> list[str]:
        return [d for d in self.core_disciplines if d not in self.disciplines()]

    def is_quorate(self) -> bool:
        """A board missing a core discipline is not quorate and its vote does not count.

        This is a question about coverage, not attendance. The failure it guards
        against is a room that agrees because nobody in it was chartered to
        raise the objection -- which looks exactly like consensus.
        """
        return not self.missing_disciplines()

    def by_phase(self) -> dict[str, list[Seat]]:
        grouped: dict[str, list[Seat]] = {phase: [] for phase in self.phases}
        for seat in self.seats:
            grouped.setdefault(seat.phase, []).append(seat)
        return grouped

    def __len__(self) -> int:
        return len(self.seats)


# The operating rules every seat receives. These are the honesty constraints,
# stated once. They are enforced structurally elsewhere -- ledger.py will not
# promote an unsupported claim no matter what a model asserts -- but stating
# them here means a well-behaved model does not have to be caught by the gate.
DEFAULT_RULES = """OPERATING RULES

A. Honesty
1. Ground every factual claim in the supplied context. If the context does not
   support a claim, say so plainly.
2. Abstain when unsure. An honest gap is a useful result; a fabricated specific
   dressed up as a finding is the worst output you can produce, because it is
   indistinguishable from a real one downstream.
3. Do not upgrade the confidence of an upstream finding. If a colleague marked
   something uncertain, it stays uncertain in your output. You may downgrade.
4. Cite by identifier. "A study found" and "industry sources suggest" are not
   citations. If you cannot name the identifier, mark the claim unsupported.
5. Never invent an identifier. A well-formed identifier attached to a claim it
   does not support is the single most damaging thing you can emit, because it
   survives every check that looks only at format.

B. Effort
6. Report coverage, not just findings. State what you searched, what you found
   nothing on, and what you could not reach. A short answer that hides thin
   coverage is worse than a long one that admits it.
7. Do not stop at the first plausible answer. Name at least one alternative
   explanation and say what would distinguish it from your preferred one.
8. If the retrieved context is thin, say "the context is thin" and specify what
   is missing. Do not pad the gap with general knowledge and do not restate the
   question back as if it were a finding.
9. Distinguish what the sources establish from what you inferred. Label
   inference as inference.

C. Disagreement
10. You are not here to agree. If an upstream seat is wrong, say so and say why,
    citing what contradicts it. Deference that suppresses a real objection is a
    failure of your charter, not politeness.
11. Where you disagree with a colleague, state the disagreement rather than
    splitting the difference. An averaged answer destroys the information that
    two competent reviewers reached different conclusions.
12. If you are asked to refute something, refute it. Do not evaluate it
    even-handedly and conclude it is probably fine.
"""


def _seat_from_dict(raw: dict[str, Any], index: int) -> Seat:
    missing = [k for k in ("key", "title", "phase", "charter") if not raw.get(k)]
    if missing:
        raise BoardError(f"seat #{index + 1}: missing required field(s): {', '.join(missing)}")

    tier = str(raw.get("tier", "light")).lower()
    if tier not in VALID_TIERS:
        raise BoardError(
            f"seat '{raw['key']}': unknown tier {tier!r} (expected one of {sorted(VALID_TIERS)})"
        )

    try:
        temperature = float(raw.get("temperature", 0.7))
    except (TypeError, ValueError) as exc:
        raise BoardError(f"seat '{raw['key']}': temperature must be a number") from exc

    context = raw.get("context") or []
    if not isinstance(context, list):
        raise BoardError(f"seat '{raw['key']}': context must be a list of seat keys")

    challenges = raw.get("challenges") or []
    if not isinstance(challenges, list):
        raise BoardError(f"seat '{raw['key']}': challenges must be a list of seat keys")

    return Seat(
        key=str(raw["key"]),
        title=str(raw["title"]),
        phase=str(raw["phase"]),
        charter=str(raw["charter"]).strip(),
        tier=tier,
        temperature=temperature,
        context=[str(c) for c in context],
        retrieval=bool(raw.get("retrieval", True)),
        challenges=[str(c) for c in challenges],
        discipline=str(raw.get("discipline", "")),
    )


def load_board(path: str | Path) -> Board:
    """Parse and validate a board definition."""
    path = Path(path)
    if not path.is_file():
        raise BoardError(f"board file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BoardError(f"{path}: invalid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise BoardError(f"{path}: top level must be a mapping")
    for required in ("name", "subject", "seats"):
        if required not in raw:
            raise BoardError(f"{path}: missing top-level key '{required}'")

    seats = [_seat_from_dict(entry, i) for i, entry in enumerate(raw["seats"] or [])]
    if not seats:
        raise BoardError(f"{path}: board defines no seats")

    keys = [s.key for s in seats]
    duplicates = {k for k in keys if keys.count(k) > 1}
    if duplicates:
        raise BoardError(f"{path}: duplicate seat key(s): {', '.join(sorted(duplicates))}")

    known = set(keys)
    position = {key: i for i, key in enumerate(keys)}
    for seat in seats:
        for ref in seat.challenges:
            if ref not in known:
                raise BoardError(
                    f"seat '{seat.key}' is chartered to challenge '{ref}', which is "
                    f"not a seat on this board."
                )
            if position[ref] >= position[seat.key]:
                raise BoardError(
                    f"seat '{seat.key}' challenges '{ref}', which is declared later. "
                    f"You cannot attack an argument that has not been made yet."
                )
        for ref in seat.context:
            if ref not in known:
                # The silent-failure case this validation exists for.
                raise BoardError(
                    f"seat '{seat.key}' declares context '{ref}', which is not a seat "
                    f"on this board. A mistyped dependency would silently deliver an "
                    f"empty context rather than failing."
                )
            if position[ref] >= position[seat.key]:
                raise BoardError(
                    f"seat '{seat.key}' depends on '{ref}', which is declared later. "
                    f"Sequential handoff requires dependencies to come first."
                )

    phases = [str(p) for p in (raw.get("phases") or [])]
    if not phases:
        seen: list[str] = []
        for seat in seats:
            if seat.phase not in seen:
                seen.append(seat.phase)
        phases = seen

    core = raw.get("core_disciplines") or []
    if not isinstance(core, list):
        raise BoardError(f"{path}: core_disciplines must be a list of discipline names")

    return Board(
        name=str(raw["name"]),
        subject=str(raw["subject"]),
        phases=phases,
        seats=seats,
        rules=str(raw.get("rules") or DEFAULT_RULES),
        core_disciplines=[str(d) for d in core],
    )
