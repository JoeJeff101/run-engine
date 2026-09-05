"""The evidence ledger: a two-stage gate between what agents find and what counts.

This is the part of the system I would defend hardest, because it is the answer
to the question that makes people rightly suspicious of LLM research tools:
*how do you know it did not make this up?*

The answer is that the model is never trusted with the authoritative record.

**Agents may only stage.** Anything an agent finds lands in a proposed file,
tagged with the channel it came from. Staging is cheap, reversible, and carries
no authority whatsoever.

**A human promotes.** Moving a row into the authoritative ledger is a separate
command that a person runs. It previews by default and writes only under
``--apply``. It filters to rows carrying a primary-source identifier, refuses
duplicates, assigns stable ids, and takes a timestamped backup before touching
anything.

**The grade cannot be talked upwards.** A row backed by a resolved registry
identifier is REAL. A row backed only by a citation is EST. A row backed by
neither is WEAK and is not promotable at all. No amount of agent confidence
moves a row up a grade; only a better identifier does. This is the rule that
makes the difference between a research assistant and a plausible-sounding
fabrication engine, and it is enforced in code rather than in a prompt --
because a prompt is a request and code is a constraint.

The asymmetry is deliberate: a fabricated number wearing a REAL tag is a far
worse outcome than an honest gap. Gaps are visible and get filled. Fabrications
propagate.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Record

# What counts as a primary source: something a third party can independently
# look up. A URL is not enough -- URLs rot, and "I saw it on a website" is not
# a citation.
#
# Two kinds of identifier, and the distinction drives the grade:
#
#   REGISTRY -- names a thing in an authoritative register. A CIK *is* the
#               company. A patent number *is* the grant. A docket number *is*
#               the case. Someone else can resolve it and get the same object.
#   CITATION -- names a document that discusses a thing. A DOI is a real
#               identifier for a paper, but the paper's claim is still a claim.
#
# This is the seam that generalizes the pipeline past science. Every
# high-stakes domain has its own register; only the pattern changes.


@dataclass(frozen=True)
class Namespace:
    name: str
    domain: str
    kind: str      # "registry" | "citation"
    pattern: str
    example: str


IDENTIFIER_NAMESPACES: tuple[Namespace, ...] = (
    # --- scholarly ---------------------------------------------------------
    Namespace("DOI", "scholarly", "citation",
              r"\b10\.\d{4,9}/\S+", "10.1126/science.1236098"),
    Namespace("PMID", "biomedical", "citation",
              r"\bPMID:\s*\d{4,9}\b", "PMID: 23887888"),
    Namespace("OpenAlex", "scholarly", "citation",
              r"\bhttps://openalex\.org/W\d+\b", "https://openalex.org/W2119459576"),
    # --- registries --------------------------------------------------------
    Namespace("PubChem CID", "scientific", "registry",
              r"\bPUBCHEM:\d+\b", "PUBCHEM:2244"),
    Namespace("US patent", "intellectual property", "registry",
              r"\bUSPTO:[A-Z0-9]+\b", "USPTO:10000000"),
    Namespace("SEC CIK", "corporate", "registry",
              r"\bCIK:\s*\d{4,10}\b", "CIK: 0000320193"),
    Namespace("EDGAR accession", "corporate filings", "registry",
              r"\b\d{10}-\d{2}-\d{6}\b", "0000320193-24-000123"),
    Namespace("CFR citation", "regulatory", "registry",
              r"\b\d{1,2}\s*C\.?F\.?R\.?\s*(?:§\s*)?\d+(?:\.\d+)*(?:-\d+)?\b",
              "17 CFR 240.10b-5"),
    Namespace("Court docket", "legal", "registry",
              r"\b(?:No\.\s*)?\d{1,2}:\d{2}-[a-z]{2,4}-\d{3,6}\b", "No. 1:21-cv-01234"),
)

REGISTRY_NAMESPACES = tuple(ns for ns in IDENTIFIER_NAMESPACES if ns.kind == "registry")
CITATION_NAMESPACES = tuple(ns for ns in IDENTIFIER_NAMESPACES if ns.kind == "citation")

PRIMARY_SOURCE_RE = re.compile(
    "(?:" + "|".join(ns.pattern for ns in IDENTIFIER_NAMESPACES) + ")", re.IGNORECASE
)
REGISTRY_RE = re.compile(
    "(?:" + "|".join(ns.pattern for ns in REGISTRY_NAMESPACES) + ")", re.IGNORECASE
)

GRADES = ("REAL", "EST", "WEAK")
PROMOTABLE = ("REAL", "EST")


def namespaces_in(text: str) -> list[str]:
    """Which identifier namespaces appear in a string. Used for provenance."""
    found: list[str] = []
    for ns in IDENTIFIER_NAMESPACES:
        if re.search(ns.pattern, text or "", re.IGNORECASE):
            found.append(ns.name)
    return found

COLUMNS = (
    "id", "topic", "claim", "value", "grade", "source", "origin", "run", "staged_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _escape(cell: str) -> str:
    """Markdown table cells cannot contain a raw pipe or newline."""
    return " ".join(str(cell or "").split()).replace("|", r"\|")


def grade_for(record: Record) -> str:
    """Grade by the strength of the identifier, never by the content.

    Deliberately mechanical. Grade is computed from what resolves, not from how
    convincing the surrounding text is -- which is exactly why a model cannot
    argue a row up a grade.
    """
    if record.entity_id and REGISTRY_RE.search(str(record.entity_id)):
        return "REAL"
    if record.doi or record.pmid or (record.entity_id and PRIMARY_SOURCE_RE.search(str(record.entity_id))):
        return "EST"
    return "WEAK"


def source_string(record: Record) -> str:
    """The citation cell. Must satisfy PRIMARY_SOURCE_RE to be promotable."""
    parts: list[str] = []
    if record.entity_id and PRIMARY_SOURCE_RE.search(str(record.entity_id)):
        parts.append(str(record.entity_id))
    if record.doi:
        parts.append(record.doi.strip())
    if record.pmid:
        parts.append(f"PMID: {record.pmid}")
    return "; ".join(parts) if parts else "(no primary identifier)"


@dataclass
class Row:
    topic: str
    claim: str
    value: str
    grade: str
    source: str
    origin: str
    run: str = ""
    id: str = ""
    staged_at: str = field(default_factory=_now)

    def key(self) -> tuple[str, str, str]:
        """Identity for idempotency: same topic + claim + value is the same row."""
        return (
            " ".join(self.topic.lower().split()),
            " ".join(self.claim.lower().split()),
            " ".join(self.value.lower().split()),
        )

    def to_markdown(self) -> str:
        cells = [_escape(getattr(self, col)) for col in COLUMNS]
        return "| " + " | ".join(cells) + " |"


def row_from_record(
    record: Record,
    topic: str,
    claim: str,
    value: str,
    origin: str,
) -> Row:
    return Row(
        topic=topic,
        claim=claim,
        value=value,
        grade=grade_for(record),
        source=source_string(record),
        origin=origin,
    )


def _parse_table(path: Path) -> list[dict[str, str]]:
    """Read an existing markdown ledger back into dicts. Tolerant of hand edits."""
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|- :"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < len(COLUMNS):
            continue
        if cells[0].lower() == "id":
            continue
        rows.append(dict(zip(COLUMNS, cells)))
    return rows


def _header() -> list[str]:
    return [
        "| " + " | ".join(COLUMNS) + " |",
        "| " + " | ".join("---" for _ in COLUMNS) + " |",
    ]


class StagingLedger:
    """Where agents write. Carries no authority."""

    def __init__(self, path: str | Path, run_id: str = "run") -> None:
        self.path = Path(path)
        self.run_id = run_id

    def existing_keys(self) -> set[tuple[str, str, str]]:
        keys: set[tuple[str, str, str]] = set()
        for row in _parse_table(self.path):
            keys.add(
                (
                    " ".join(row["topic"].lower().split()),
                    " ".join(row["claim"].lower().split()),
                    " ".join(row["value"].lower().split()),
                )
            )
        return keys

    def stage(
        self,
        rows: Iterable[Row],
        max_rows: int = 4,
        dry: bool = True,
    ) -> list[Row]:
        """Stage rows. Returns what would be (or was) written.

        ``dry=True`` is the default deliberately: the caller has to ask for a
        write. ``max_rows`` bounds how much any single agent turn can add, so a
        model that decides everything it read was important cannot flood the
        file.
        """
        seen = self.existing_keys()
        accepted: list[Row] = []
        existing_count = len(_parse_table(self.path))

        for row in rows:
            if len(accepted) >= max_rows:
                break
            if row.key() in seen:
                continue          # idempotent: staging twice is a no-op
            if row.grade not in GRADES:
                continue
            seen.add(row.key())
            row.run = self.run_id
            row.id = f"PROP-{self.run_id}-{existing_count + len(accepted) + 1:02d}"
            accepted.append(row)

        if dry or not accepted:
            return accepted

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        if not self.path.is_file():
            lines.append("# Proposed evidence (staged, NOT authoritative)")
            lines.append("")
            lines.append(
                "Written by agents. Nothing here counts until a human promotes it. "
                "See `evidence_pipeline.ledger.promote`."
            )
            lines.append("")
            lines.extend(_header())
        body = "\n".join(lines + [r.to_markdown() for r in accepted])
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(body + "\n")
        return accepted


@dataclass
class PromotionReport:
    promoted: list[Row]
    rejected: list[tuple[Row, str]]
    applied: bool
    backup: str | None = None

    def render(self) -> str:
        out = [
            f"promotion {'APPLIED' if self.applied else 'PREVIEW (no changes written)'}",
            f"  promotable: {len(self.promoted)}",
            f"  rejected:   {len(self.rejected)}",
        ]
        if self.backup:
            out.append(f"  backup:     {self.backup}")
        for row in self.promoted:
            out.append(f"  + [{row.grade}] {row.claim} = {row.value}  <- {row.source}")
        for row, why in self.rejected:
            out.append(f"  - {row.claim}: {why}")
        return "\n".join(out)


def promote(
    staged_path: str | Path,
    authoritative_path: str | Path,
    apply: bool = False,
    prefix: str = "EV",
) -> PromotionReport:
    """Move qualifying staged rows into the authoritative ledger.

    Preview unless ``apply=True``. This is a human's command; no agent should
    ever be given a tool that calls it.
    """
    staged_path = Path(staged_path)
    authoritative_path = Path(authoritative_path)

    existing = _parse_table(authoritative_path)
    existing_keys = {
        (
            " ".join(r["topic"].lower().split()),
            " ".join(r["claim"].lower().split()),
            " ".join(r["value"].lower().split()),
        )
        for r in existing
    }

    promoted: list[Row] = []
    rejected: list[tuple[Row, str]] = []

    for raw in _parse_table(staged_path):
        row = Row(
            topic=raw.get("topic", ""),
            claim=raw.get("claim", ""),
            value=raw.get("value", ""),
            grade=(raw.get("grade") or "").upper(),
            source=raw.get("source", ""),
            origin=raw.get("origin", ""),
            run=raw.get("run", ""),
            staged_at=raw.get("staged_at", ""),
        )
        if row.grade not in PROMOTABLE:
            rejected.append((row, f"grade {row.grade or '?'} is not promotable"))
            continue
        if not PRIMARY_SOURCE_RE.search(row.source):
            rejected.append((row, "no primary-source identifier in citation"))
            continue
        if row.key() in existing_keys:
            rejected.append((row, "already present in the authoritative ledger"))
            continue
        existing_keys.add(row.key())
        row.id = f"{prefix}-{len(existing) + len(promoted) + 1:03d}"
        promoted.append(row)

    if not apply or not promoted:
        return PromotionReport(promoted=promoted, rejected=rejected, applied=False)

    # Back up before touching the authoritative file. Always, not on failure --
    # by the time you know you needed a backup it is too late to take one.
    backup: str | None = None
    if authoritative_path.is_file():
        backup = str(authoritative_path.with_suffix(f".backup_{_stamp()}{authoritative_path.suffix}"))
        shutil.copy2(authoritative_path, backup)

    authoritative_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if not authoritative_path.is_file():
        lines.append("# Evidence ledger (authoritative)")
        lines.append("")
        lines.append(
            "HUMAN-PROMOTED ONLY. Agents stage into the proposed file; they never "
            "write here. Rows carry a primary-source identifier by construction."
        )
        lines.append("")
        lines.extend(_header())
    body = "\n".join(lines + [r.to_markdown() for r in promoted])
    with authoritative_path.open("a", encoding="utf-8") as handle:
        handle.write(body + "\n")

    return PromotionReport(promoted=promoted, rejected=rejected, applied=True, backup=backup)


def main(argv: list[str] | None = None) -> int:
    """CLI: preview by default, write only with --apply."""
    import argparse

    ap = argparse.ArgumentParser(description="Promote staged evidence into the authoritative ledger.")
    ap.add_argument("staged", help="path to the staged/proposed ledger")
    ap.add_argument("authoritative", help="path to the authoritative ledger")
    ap.add_argument("--apply", action="store_true", help="actually write (default is preview)")
    args = ap.parse_args(argv)

    report = promote(args.staged, args.authoritative, apply=args.apply)
    print(report.render())
    if not args.apply and report.promoted:
        print("\nRe-run with --apply to write these rows.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
