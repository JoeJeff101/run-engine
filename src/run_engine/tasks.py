"""The work chain: an ordered list of tasks, with three flags that matter.

A board is a roster of *actors*. This is the list of *deliverables* they
produce, which is a different thing and was previously conflated. Seats argue;
tasks are what the arguing is supposed to result in.

Three flags carry real semantics rather than emphasis:

``crux``
    The single make-or-break question. Exactly one per chain. Everything
    downstream is worthless if this fails, which is why it is named, flagged,
    and tested first rather than discovered in month five.

``core``
    If this task is wrong, its whole phase is invalid. Not "important" —
    load-bearing.

``finalize``
    The task where the work leaves its authors and enters adjudication. Its
    owner may own no other task in the chain, enforced here, because a system
    that grades its own homework produces exactly the grade it wants.

In the system this generalizes, these three properties existed only as bold
text inside task descriptions — ``**THE CRUX.**`` in one file, ``**This is the
FINALIZE task.**`` in another. Nothing parsed them, so nothing could enforce
them. Promoting them to schema is most of the value of this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

TASK_ID = re.compile(r"^T\d{2,}$")


class TaskError(ValueError):
    """Raised when a work chain is malformed."""


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    seat: str
    phase: str
    description: str = ""
    core: bool = False
    crux: bool = False
    finalize: bool = False

    def __post_init__(self) -> None:
        if not TASK_ID.match(self.id):
            raise TaskError(f"task id {self.id!r} must look like T01, T02, ...")
        if not self.title.strip():
            raise TaskError(f"{self.id}: needs a title")
        if not self.seat.strip():
            raise TaskError(
                f"{self.id}: needs an owning seat. An unowned task is one nobody "
                f"is accountable for."
            )
        if self.crux and self.finalize:
            raise TaskError(
                f"{self.id}: cannot be both the crux and the finalize task — the "
                f"question that decides everything cannot also be the check on it."
            )


@dataclass(frozen=True)
class TaskChain:
    tasks: tuple[Task, ...]

    def __post_init__(self) -> None:
        if not self.tasks:
            raise TaskError("work chain is empty")

        ids = [t.id for t in self.tasks]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise TaskError(f"duplicate task id(s): {', '.join(sorted(dupes))}")
        if ids != sorted(ids):
            raise TaskError(
                f"tasks are out of order: {', '.join(ids)}. The chain is strictly "
                f"linear; declare them in the order they run."
            )

        cruxes = [t.id for t in self.tasks if t.crux]
        if len(cruxes) != 1:
            raise TaskError(
                f"expected exactly one crux task, found {len(cruxes)}"
                + (f" ({', '.join(cruxes)})" if cruxes else "") + ". "
                "If you cannot name the single question that makes everything else "
                "moot, you are not ready to spend money on the rest."
            )

        finals = [t for t in self.tasks if t.finalize]
        if len(finals) != 1:
            raise TaskError(
                f"expected exactly one finalize task, found {len(finals)}. Someone "
                f"accountable has to run the rule gates and sign the dossier."
            )
        if finals[0].id != self.tasks[-1].id:
            raise TaskError(
                f"the finalize task ({finals[0].id}) must be last in the chain; "
                f"{self.tasks[-1].id} comes after it."
            )

        adjudicator = finals[0].seat
        also_generates = [t.id for t in self.tasks if t.seat == adjudicator and not t.finalize]
        if also_generates:
            raise TaskError(
                f"seat {adjudicator!r} runs the finalize task and also owns "
                f"{', '.join(also_generates)}. Generation and adjudication must not "
                f"share an actor — separation of duties is the point of the finalize "
                f"step, and a reviewer marking their own work is not a review."
            )

    @property
    def crux(self) -> Task:
        return next(t for t in self.tasks if t.crux)

    @property
    def finalize(self) -> Task:
        return next(t for t in self.tasks if t.finalize)

    @property
    def core(self) -> tuple[Task, ...]:
        return tuple(t for t in self.tasks if t.core)

    @property
    def generators(self) -> tuple[Task, ...]:
        """Every task except the adjudication step."""
        return tuple(t for t in self.tasks if not t.finalize)

    def by_phase(self) -> dict[str, list[Task]]:
        grouped: dict[str, list[Task]] = {}
        for task in self.tasks:
            grouped.setdefault(task.phase, []).append(task)
        return grouped

    def task(self, task_id: str) -> Task | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def __len__(self) -> int:
        return len(self.tasks)

    def __iter__(self):
        return iter(self.tasks)

    def digest(self) -> str:
        out = []
        for t in self.tasks:
            flags = "".join([
                " [CRUX]" if t.crux else "",
                " [core]" if t.core else "",
                " [FINALIZE]" if t.finalize else "",
            ])
            out.append(f"  {t.id} {t.title} ({t.seat}){flags}")
        return "\n".join(out)

    @classmethod
    def load(cls, path: str | Path) -> "TaskChain":
        p = Path(path)
        if not p.is_file():
            raise TaskError(f"task chain file not found: {p}")
        try:
            raw: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise TaskError(f"{p}: invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise TaskError(f"{p}: top level must be a mapping")

        entries = raw.get("tasks") or []
        if not isinstance(entries, list):
            raise TaskError(f"{p}: 'tasks' must be a list")

        tasks = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise TaskError(f"{p}: each task must be a mapping")
            tasks.append(Task(
                id=str(entry.get("id", "")),
                title=str(entry.get("title", "")),
                seat=str(entry.get("seat", "")),
                phase=str(entry.get("phase", "")),
                description=str(entry.get("description", "")).strip(),
                core=bool(entry.get("core", False)),
                crux=bool(entry.get("crux", False)),
                finalize=bool(entry.get("finalize", False)),
            ))
        return cls(tasks=tuple(tasks))
