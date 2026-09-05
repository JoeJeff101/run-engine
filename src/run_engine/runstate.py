"""Carry-forward: immutable run folders, open items, and the plateau flag.

Three things persist between runs, and they are deliberately different kinds
of thing:

**Run folders** are the archive. Every run gets one, pass or fail, and once
sealed it is never edited. Failed runs are archived too -- they are the
diligence trail, and a system that keeps only its successes has no way to show
anyone how it reached its conclusion.

**Continuity** is the working memory: what is unresolved, what is blocked, and
whether the last few runs actually moved. Continuity is how the next run starts
knowing what this one could not settle.

**The evidence ledger** (in ``evidence/``) is the record of what is known. It
is the only one of the three that may move a probability.

The plateau flag
----------------
The most useful and least flattering signal here. When three consecutive runs
produce the same headline and the open items do not shrink, more analysis is
not the constraint -- money or access is. Detecting that automatically matters
because the alternative is discovering it after the fourth identical meeting,
and a board will always prefer to re-run the analysis over admitting the
analysis is finished.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SEAL_MARKER = ".sealed"
RUN_ID = re.compile(r"^\d{8}T\d{6}Z(-\w+)?$")


class RunSealed(RuntimeError):
    """Raised on any attempt to write to a finalized run folder."""


class RunStateError(ValueError):
    """Raised when run state on disk is malformed."""


# ---------------------------------------------------------------------------
# Open items
# ---------------------------------------------------------------------------


@dataclass
class OpenItem:
    """Something the run could not settle, carried into the next one."""

    id: str
    question: str
    blocked_on: str = ""   # what would settle it: a document, a quote, a test
    opened_run: str = ""
    status: str = "open"   # open | blocked | closed

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "question": self.question, "blocked_on": self.blocked_on,
                "opened_run": self.opened_run, "status": self.status}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "OpenItem":
        return cls(
            id=str(raw.get("id", "")), question=str(raw.get("question", "")),
            blocked_on=str(raw.get("blocked_on", "")), opened_run=str(raw.get("opened_run", "")),
            status=str(raw.get("status", "open")),
        )


@dataclass
class Continuity:
    """What carries forward, plus the honest flag about whether it moved."""

    open_items: list[OpenItem] = field(default_factory=list)
    headline: float | None = None
    plateau: bool = False
    note: str = ""

    def unresolved(self) -> list[OpenItem]:
        return [i for i in self.open_items if i.status != "closed"]

    def to_markdown(self, run_id: str) -> str:
        lines = [f"# Continuity — run {run_id}", ""]
        if self.headline is not None:
            lines += [f"Headline P(success): **{self.headline:.1%}**", ""]
        if self.plateau:
            lines += [
                "> **PLATEAU.** The last three runs did not move the headline and the open "
                "items did not shrink. The boardroom cannot resolve these by reasoning. "
                "The constraint is money or access, not analysis — fund the top-ranked "
                "experiment or accept the current estimate.", "",
            ]
        if self.note:
            lines += [self.note, ""]
        lines += ["## Open items", ""]
        if not self.unresolved():
            lines.append("_None carried forward._")
        for item in self.unresolved():
            blocked = f" — blocked on: {item.blocked_on}" if item.blocked_on else ""
            lines.append(f"- **{item.id}** ({item.status}) {item.question}{blocked}")
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict[str, Any]:
        return {"open_items": [i.to_dict() for i in self.open_items],
                "headline": self.headline, "plateau": self.plateau, "note": self.note}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Continuity":
        return cls(
            open_items=[OpenItem.from_dict(i) for i in raw.get("open_items") or []],
            headline=raw.get("headline"),
            plateau=bool(raw.get("plateau", False)),
            note=str(raw.get("note", "")),
        )


def detect_plateau(
    history: Iterable[tuple[float | None, int]], *, window: int = 3, tolerance: float = 0.02
) -> bool:
    """Three runs, same number, no fewer questions.

    ``history`` is (headline, unresolved_count) oldest-last -- i.e. the most
    recent run first. Both conditions are required: a headline that holds still
    while open items fall is progress, and a headline that moves while items
    stall is at least information.
    """
    recent = [h for h in list(history)[:window]]
    if len(recent) < window:
        return False
    headlines = [h for h, _ in recent]
    if any(h is None for h in headlines):
        return False
    if max(headlines) - min(headlines) > tolerance:  # type: ignore[type-var]
        return False
    counts = [c for _, c in recent]
    # counts[0] is the newest. Shrinking means newest < oldest.
    return counts[0] >= counts[-1]


# ---------------------------------------------------------------------------
# Run folders
# ---------------------------------------------------------------------------


@dataclass
class RunFolder:
    """One immutable per-run archive.

    Writes are allowed until ``seal()``; after that every write raises. The
    seal exists because an archive that can be revised after the fact is a
    draft, and a draft cannot be evidence of what was believed at the time.
    """

    root: Path
    run_id: str

    @classmethod
    def create(cls, runs_dir: str | Path, *, run_id: str | None = None,
               at: datetime | None = None, suffix: str = "") -> "RunFolder":
        stamp = (at or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        rid = run_id or (f"{stamp}-{suffix}" if suffix else stamp)
        root = Path(runs_dir) / rid
        n = 1
        while root.exists():  # two runs in the same second must not collide
            n += 1
            rid = f"{stamp}-{n}" if not suffix else f"{stamp}-{suffix}-{n}"
            root = Path(runs_dir) / rid
        root.mkdir(parents=True)
        return cls(root=root, run_id=rid)

    @property
    def sealed(self) -> bool:
        return (self.root / SEAL_MARKER).exists()

    def path(self, name: str) -> Path:
        return self.root / name

    def write(self, name: str, content: str) -> Path:
        if self.sealed:
            raise RunSealed(
                f"run {self.run_id} is sealed; refusing to write {name!r}. A finalized "
                f"run is the record of what was believed at the time — start a new run "
                f"rather than revising this one."
            )
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def write_json(self, name: str, payload: dict[str, Any]) -> Path:
        return self.write(name, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def append_jsonl(self, name: str, payload: dict[str, Any]) -> Path:
        if self.sealed:
            raise RunSealed(f"run {self.run_id} is sealed; refusing to append to {name!r}")
        target = self.path(name)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
        return target

    def read(self, name: str) -> str:
        return self.path(name).read_text(encoding="utf-8")

    def seal(self) -> Path:
        marker = self.path(SEAL_MARKER)
        marker.write_text(
            datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n", encoding="utf-8")
        return marker

    def contents(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.name != SEAL_MARKER)


def load_runs(runs_dir: str | Path) -> list[dict[str, Any]]:
    """Every archived run's ``run.json``, newest first.

    Malformed or partial runs are skipped rather than raising: a crashed run
    should not stop the next one from starting, and its folder is still on
    disk for a human to look at.
    """
    root = Path(runs_dir)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), reverse=True):
        meta = entry / "run.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            data.setdefault("run_id", entry.name)
            out.append(data)
    return out


def history_for_plateau(runs: Iterable[dict[str, Any]]) -> list[tuple[float | None, int]]:
    """Reduce archived runs to the two series the plateau test needs."""
    series: list[tuple[float | None, int]] = []
    for run in runs:
        headline = run.get("probability", {}).get("mean") if isinstance(run.get("probability"), dict) else None
        count = len(run.get("open_items") or [])
        series.append((headline, count))
    return series


def latest_continuity(runs_dir: str | Path) -> Continuity:
    """The continuity a new run should ground itself on."""
    runs = load_runs(runs_dir)
    if not runs:
        return Continuity()
    newest = runs[0]
    return Continuity(
        open_items=[OpenItem.from_dict(i) for i in newest.get("open_items") or []],
        headline=(newest.get("probability") or {}).get("mean"),
        plateau=bool(newest.get("plateau", False)),
    )
