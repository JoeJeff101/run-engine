"""A pack: one complete instantiation of the engine, as files.

Everything domain-specific lives in a pack directory, and the engine contains
none of it. Swapping the pack swaps the subject matter; the governance, the
freeze, the gates, the ledger and the arithmetic are unchanged, because they
were never about the subject in the first place.

    pack.yaml         name and one-line description
    brief.yaml        the two-clause north star
    contract.yaml     rules of claim-making
    spec.yaml         the constitution: master metric, gates, priors, changelog
    substrate.yaml    the nine-stage state machine of the thing being acted on
    tasks.yaml        the work chain, with core / crux / finalize flags
    board.yaml        the roster of seats and their charters
    sources.yaml      declared data sources and their evidence classes
    experiments.yaml  the candidate experiment queue, for value-of-information
    personas/         optional per-seat background, one markdown file per seat

Cross-artifact validation
-------------------------
Each file validates itself on load. The interesting failures, though, are
*between* files: a task owned by a seat that is not on the board, a gate the
dossier will never mention, an experiment informing a term with no prior. Those
produce silence rather than errors -- the seat is simply never asked, the
experiment is simply never ranked -- and silence is the failure mode this whole
engine is built to make loud. So they are checked here, at load, before a run
can spend anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .agents.board import Board, BoardError, load_board
from .datalayer import SourceRegistry
from .probability import MARKET
from .spec.model import Brief, Contract, Spec
from .substrate import Substrate
from .tasks import TaskChain
from .voi import Experiment, load_experiments


class PackError(ValueError):
    """Raised when a pack is incomplete or internally inconsistent."""


REQUIRED = ("brief.yaml", "contract.yaml", "spec.yaml", "substrate.yaml",
            "tasks.yaml", "board.yaml", "sources.yaml")


@dataclass
class Pack:
    root: Path
    name: str
    description: str
    brief: Brief
    contract: Contract
    spec: Spec
    substrate: Substrate
    tasks: TaskChain
    board: Board
    sources: SourceRegistry
    experiments: list[Experiment] = field(default_factory=list)
    personas: dict[str, str] = field(default_factory=dict)

    # -- grounding ----------------------------------------------------------

    def grounding(self, seat_key: str = "", *, ledger_digest: str = "",
                  continuity: str = "") -> str:
        """The four artifacts every agent is re-loaded with, every run.

        Brief, current spec, persona, ledger -- plus whatever last run could not
        resolve. Continuity comes from artifacts, never from memory, so this
        string is assembled fresh each time rather than carried in a session.
        """
        parts = [
            f"# Grounding — {self.name} (spec v{self.spec.version})",
            "",
            "## Brief",
            self.brief.one_line(),
            "",
            "## Evidence discipline",
            self.contract.as_prompt(),
            "",
            "## Current spec",
            self.spec.digest(),
            "",
            "## Substrate",
            self.substrate.digest(),
        ]
        if seat_key and self.personas.get(seat_key):
            parts += ["", "## Your background", self.personas[seat_key].strip()]
        # Always present, even when empty. An agent grounded without a ledger
        # section has no way to tell "nothing is established" from "nobody told
        # me", and those produce very different documents.
        parts += ["", "## Evidence ledger", ledger_digest or (
            "The ledger is empty. Nothing is established yet, so every number in this "
            "run is an assumption and must be tagged as one.")]
        if continuity:
            parts += ["", "## Carried forward from the last run", continuity]
        return "\n".join(parts) + "\n"

    # -- loading ------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Pack":
        root = Path(path)
        if not root.is_dir():
            raise PackError(f"pack directory not found: {root}")

        missing = [f for f in REQUIRED if not (root / f).is_file()]
        if missing:
            raise PackError(
                f"{root.name}: incomplete pack, missing {', '.join(missing)}. "
                f"Every one of these is load-bearing; a pack without a brief has no "
                f"win condition, and a pack without sources cannot produce a REAL row."
            )

        meta: dict[str, Any] = {}
        meta_path = root / "pack.yaml"
        if meta_path.is_file():
            loaded = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                meta = loaded

        try:
            board = load_board(root / "board.yaml")
        except BoardError as exc:
            raise PackError(f"{root.name}: {exc}") from exc

        spec = Spec.load(root / "spec.yaml")
        # The board inherits its quorum from the constitution unless it declares
        # its own -- so "who must be in the room" is a governed decision.
        if not board.core_disciplines and spec.core_disciplines:
            board.core_disciplines = list(spec.core_disciplines)

        personas: dict[str, str] = {}
        persona_dir = root / "personas"
        if persona_dir.is_dir():
            for entry in sorted(persona_dir.glob("*.md")):
                personas[entry.stem] = entry.read_text(encoding="utf-8")

        experiments: list[Experiment] = []
        if (root / "experiments.yaml").is_file():
            experiments = load_experiments(root / "experiments.yaml")

        pack = cls(
            root=root,
            name=str(meta.get("name") or root.name),
            description=str(meta.get("description", "")),
            brief=Brief.load(root / "brief.yaml"),
            contract=Contract.load(root / "contract.yaml"),
            spec=spec,
            substrate=Substrate.load(root / "substrate.yaml"),
            tasks=TaskChain.load(root / "tasks.yaml"),
            board=board,
            sources=SourceRegistry.load(root / "sources.yaml"),
            experiments=experiments,
            personas=personas,
        )
        pack.validate()
        return pack

    # -- cross-artifact checks ---------------------------------------------

    def validate(self) -> None:
        problems: list[str] = []

        seat_keys = {s.key for s in self.board.seats}
        for task in self.tasks:
            if task.seat not in seat_keys:
                problems.append(
                    f"task {task.id} is owned by seat {task.seat!r}, which is not on the "
                    f"board. That task would never be run and nothing would say so.")

        if not self.board.is_quorate():
            problems.append(
                f"the board does not cover every core discipline; missing: "
                f"{', '.join(self.board.missing_disciplines())}. A board missing a "
                f"discipline is not quorate and its vote does not count.")

        gate_ids = {g.id for g in self.spec.gates}
        for exp in self.experiments:
            if exp.term not in gate_ids | {MARKET}:
                problems.append(
                    f"experiment {exp.id} informs {exp.term!r}, which is neither a "
                    f"declared gate nor the market term, so it can never be ranked.")

        declared_feeds = {f for s in self.sources.sources for f in s.feeds}
        task_ids = {t.id for t in self.tasks}
        for feed in sorted(declared_feeds):
            if feed not in task_ids | gate_ids:
                problems.append(
                    f"a source declares that it feeds {feed!r}, which is neither a task "
                    f"nor a gate in this pack.")

        if problems:
            raise PackError(
                f"{self.name}: the pack's files disagree with each other:\n  - "
                + "\n  - ".join(problems))

    # -- convenience --------------------------------------------------------

    @property
    def lock_task(self) -> str:
        """Where a failed gate routes back to: the task that designs the lock.

        Defaults to the first core task, because mechanism design is where a
        failure is actually fixed; falls back to the crux if none is flagged.
        """
        core = self.tasks.core
        return core[0].id if core else self.tasks.crux.id

    def gate_ids(self) -> list[str]:
        return [g.id for g in sorted(self.spec.gates, key=lambda g: g.cost)]


def available_packs(root: str | Path = "packs") -> list[str]:
    base = Path(root)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if (p / "spec.yaml").is_file())
