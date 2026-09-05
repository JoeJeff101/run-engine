"""The data layer: which sources a pack may read, and what each return is worth.

The engine is domain-neutral, so it ships no opinion about which databases
matter. A pack declares its own: the datasets that match its industry, its
question, and the registry that settles disputes in its field. Wiring a new
domain is adding entries to ``sources.yaml`` and supplying the keys they need.

The rule that governs the swap is not "which API" but **which class of return
is allowed to be promoted to REAL**, and that decision lives here, attached to
the source, rather than anywhere an agent can reach:

    real   a primary record with a retrievable document, or a reproducible
           query against an authoritative register -- a government dataset, a
           regulatory filing, a signed quote, your own bank or platform export,
           a completed test.

    est    everything else -- aggregator estimates, scraped inference, analyst
           reports, vendor marketing, and *any* number a language model
           produced without a citation.

An aggregator that estimates a category's revenue is EST forever, however
confident it looks. A customs record naming a real shipment is REAL. Getting
that line right is most of the value of the whole system, which is why the
class is a property of the source and the agent is never asked for it.

Credentials
-----------
A source whose key is absent reports ``needs key`` and skips. It does not
fabricate, and -- importantly -- it does not silently downgrade itself to EST
and carry on. A missing credential is a gap in coverage, and a gap you can see
is worth more than a number you cannot trace.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

REAL, EST = "REAL", "EST"
CLASSES = (REAL, EST)

WIRED, NEEDS_KEY, MANUAL, UNWIRED = "wired", "needs key", "manual", "not wired"


class DataLayerError(ValueError):
    """Raised when a source declaration is malformed or unknown."""


@dataclass(frozen=True)
class SourceDecl:
    """One declared source. The class is declared here and nowhere else."""

    name: str
    evidence_class: str
    answers: str = ""          # what question this source settles
    key_env: str = ""          # environment variable holding its credential
    endpoint: str = ""
    adapter: str = ""          # a built-in retrieval adapter, when one exists
    manual: bool = False       # terms don't permit automation; runs as a human queue
    feeds: tuple[str, ...] = ()  # which tasks or gates it serves
    note: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise DataLayerError("source: needs a name")
        if self.evidence_class.upper() not in CLASSES:
            raise DataLayerError(
                f"source {self.name!r}: evidence class must be REAL or EST, got "
                f"{self.evidence_class!r}. There is no third class and no 'probably'."
            )
        object.__setattr__(self, "evidence_class", self.evidence_class.upper())

    def status(self, env: dict[str, str] | None = None) -> str:
        """Whether this source can actually be called right now."""
        env = os.environ if env is None else env
        if self.manual:
            return MANUAL
        if self.key_env and not env.get(self.key_env):
            return f"{NEEDS_KEY}: {self.key_env}"
        if not self.adapter:
            return UNWIRED
        return WIRED

    def available(self, env: dict[str, str] | None = None) -> bool:
        return self.status(env) == WIRED


@dataclass(frozen=True)
class SourceRegistry:
    """Every source a pack declares, and the only place an evidence class comes from."""

    sources: tuple[SourceDecl, ...] = ()

    def __post_init__(self) -> None:
        names = [s.name for s in self.sources]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise DataLayerError(f"duplicate source declaration(s): {', '.join(sorted(dupes))}")

    def get(self, name: str) -> SourceDecl:
        found = next((s for s in self.sources if s.name.lower() == name.lower()), None)
        if found is None:
            raise DataLayerError(
                f"unknown source {name!r}. A finding can only be classified if its "
                f"source is declared in the pack — otherwise the classification would "
                f"be coming from whoever reported the finding, which is exactly what "
                f"this table exists to prevent."
            )
        return found

    def classify(self, name: str) -> str:
        """The evidence class for a finding, assigned by its source.

        Deliberately takes a source name and nothing else. There is no argument
        by which a caller can propose a class, because a caller that could
        propose one could propose REAL.
        """
        return self.get(name).evidence_class

    def wired(self, env: dict[str, str] | None = None) -> tuple[SourceDecl, ...]:
        return tuple(s for s in self.sources if s.available(env))

    def blocked(self, env: dict[str, str] | None = None) -> tuple[tuple[SourceDecl, str], ...]:
        return tuple((s, s.status(env)) for s in self.sources if not s.available(env))

    def adapters(self, env: dict[str, str] | None = None) -> list[str]:
        return [s.adapter for s in self.wired(env) if s.adapter]

    def table(self, env: dict[str, str] | None = None) -> str:
        """The wiring table. This is what tells a user which keys to go and get."""
        lines = [
            "| Source | Class | Status | Answers |",
            "|---|---|---|---|",
        ]
        for s in self.sources:
            lines.append(f"| {s.name} | {s.evidence_class} | {s.status(env)} | {s.answers} |")
        ready = len(self.wired(env))
        lines += [
            "",
            f"{ready} of {len(self.sources)} sources are callable in this environment.",
        ]
        missing = sorted({
            s.key_env for s in self.sources
            if s.key_env and not (os.environ if env is None else env).get(s.key_env)
        })
        if missing:
            lines.append(
                "Set these to widen coverage: " + ", ".join(missing) + ". "
                "A source without its key skips rather than guessing, so the effect of "
                "a missing key is a visible gap, never a quiet estimate."
            )
        manual = [s.name for s in self.sources if s.manual]
        if manual:
            lines.append(
                "Human queue (terms do not permit automation): " + ", ".join(manual) + ". "
                "The engine emits the exact search strings; a person runs them and the "
                "results return through the same staging path with the same identifier "
                "requirements."
            )
        return "\n".join(lines)

    @classmethod
    def load(cls, path: str | Path) -> "SourceRegistry":
        p = Path(path)
        if not p.is_file():
            raise DataLayerError(f"source registry not found: {p}")
        try:
            raw: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise DataLayerError(f"{p}: invalid YAML: {exc}") from exc

        entries = raw.get("sources") if isinstance(raw, dict) else raw
        if not entries:
            raise DataLayerError(
                f"{p}: no sources declared. A pack that declares no sources cannot "
                f"produce a REAL row, and a run that cannot produce a REAL row cannot "
                f"move its own probability."
            )
        decls = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise DataLayerError(f"{p}: each source must be a mapping")
            decls.append(SourceDecl(
                name=str(entry.get("name", "")),
                evidence_class=str(entry.get("class", entry.get("evidence_class", ""))),
                answers=str(entry.get("answers", "")),
                key_env=str(entry.get("key_env", "")),
                endpoint=str(entry.get("endpoint", "")),
                adapter=str(entry.get("adapter", "")),
                manual=bool(entry.get("manual", False)),
                feeds=tuple(str(f) for f in entry.get("feeds", []) or []),
                note=str(entry.get("note", "")),
            ))
        return cls(sources=tuple(decls))


def rows_from_findings(
    registry: SourceRegistry,
    findings: Iterable[tuple[str, str, str, str]],
    *,
    run_id: str = "",
):
    """Turn (source, topic, claim, value) tuples into ledger rows.

    The grade is looked up from the registry rather than accepted from the
    caller. This is the software equivalent of the promotion script: the agent
    reports what it found and where; the class is assigned by the table.
    """
    from .evidence.ledger import Row

    out = []
    for source_name, topic, claim, value in findings:
        decl = registry.get(source_name)
        out.append(Row(
            topic=topic, claim=claim, value=value,
            grade=decl.evidence_class,
            source=decl.endpoint or decl.name,
            origin=decl.name,
            run=run_id,
        ))
    return out
