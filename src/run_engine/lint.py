"""Structural validation of a compiled dossier. Deterministic, and no model.

This module deliberately imports nothing that can think. A validator able to
call a language model is a validator that can be argued out of its finding,
and the whole point of running the linter *after* the red team is that one of
the two checks should not be persuadable.

Everything here is regex-level and mechanical: does every figure carry a
source, does every REAL row carry a document, do the totals add up, did every
declared gate record a result. None of that requires judgment, and any of it
failing means the dossier is not yet a document anyone should rely on.

Findings are either **blocking** or **advisory**. Blocking findings are ones
where the dossier asserts something it has not earned; advisory findings are
where it is merely sloppy. The distinction matters because the run loop feeds
blocking findings into the master metric -- a plan with unsourced numbers
cannot score well -- while advisory findings are printed and left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from .evidence.ledger import PRIMARY_SOURCE_RE

BLOCKING, ADVISORY = "blocking", "advisory"

# A figure worth sourcing: money, percentages, or a number with a magnitude.
# Deliberately not "any digit" -- list numbering, dates and section ids are not
# claims, and a linter that cries wolf gets switched off.
# Both guards matter. The leading one keeps us out of the middle of a token; the
# trailing one keeps identifiers from reading as quantities -- a run id like
# 20260905T142839Z and a model digest like 4176222d both contain long digit runs
# and neither is a claim about the world.
FIGURE = re.compile(
    r"(?<![\w.])(?:[$£€]\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmMbB])?"
    r"|\d[\d,]*(?:\.\d+)?\s?%"
    r"|\d[\d,]{3,}(?:\.\d+)?)(?![\w])"
)
TAGGED = re.compile(r"\b(REAL|EST|WEAK)\b")
# "per" needs a determiner after it: "per the 10-K" is a citation, "per unit" is
# a unit. An earlier version accepted a bare "per" and silently excused every
# line containing the phrase "per unit" -- which, in a costing dossier, is most
# of them.
SOURCE_HINT = re.compile(
    r"\b(?:source|src|cite[ds]?|invoice|quote|filing|export)\b[:\s]"
    r"|\bper\s+(?:the|our|its|their|a|an)\b", re.I)
DOCUMENT_HINT = re.compile(
    r"\b(invoice|quote|contract|statement|export|report|certificate|filing|receipt|"
    r"screenshot|dataset|\.pdf|\.csv|\.xlsx)\b", re.I)

# Claims that sound sourced and are not. Each of these phrases has, at some
# point, carried a number into a document with nothing behind it.
WEASEL = re.compile(
    r"\b(studies show|research suggests?|industry sources|it is (?:widely )?known|"
    r"experts? (?:say|agree)|generally accepted|commonly cited|market data indicates?)\b", re.I)

GATE_RESULT = re.compile(r"\b(PASS|FAIL|PENDING|DEFUNDED|GREEN|RED|NOT RUN)\b", re.I)
HEADLINE = re.compile(r"P\(success\)[^0-9]{0,20}(\d+(?:\.\d+)?)\s?%", re.I)
CODE_FENCE = re.compile(r"^\s*```")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    line: int
    message: str
    excerpt: str = ""

    def __str__(self) -> str:
        where = f"line {self.line}" if self.line else "document"
        return f"[{self.severity.upper()}] {self.rule} ({where}): {self.message}"


@dataclass
class LintReport:
    findings: tuple[Finding, ...] = ()
    suppressed_lines: int = 0

    @property
    def blocking(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == BLOCKING)

    @property
    def advisory(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity == ADVISORY)

    @property
    def ok(self) -> bool:
        """True when nothing blocking was found. Advisory findings do not fail a run."""
        return not self.blocking

    def to_markdown(self) -> str:
        lines = ["# Lint report", ""]
        lines.append(
            f"{len(self.blocking)} blocking, {len(self.advisory)} advisory."
            if self.findings else "No findings. Every figure carries a source, every REAL "
            "row carries a document, totals reconcile, and every gate recorded a result."
        )
        if self.suppressed_lines:
            lines.append(
                f"{self.suppressed_lines} line(s) were inside `lint:off` regions and were "
                f"not checked. Those regions should contain only provenance and telemetry; "
                f"if a claim is hiding in one, this line is how you find out.")
        lines.append("")
        for severity, group in ((BLOCKING, self.blocking), (ADVISORY, self.advisory)):
            if not group:
                continue
            lines += [f"## {severity.title()}", ""]
            for f in group:
                lines.append(f"- **{f.rule}** (line {f.line}): {f.message}")
                if f.excerpt:
                    lines.append(f"  > `{f.excerpt.strip()[:160]}`")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


LINT_OFF = re.compile(r"<!--\s*lint:off\s*-->")
LINT_ON = re.compile(r"<!--\s*lint:on\s*-->")


def _content_lines(text: str) -> tuple[list[tuple[int, str]], int]:
    """Numbered lines, minus fenced code and explicitly suppressed regions.

    Two exclusions, for two different reasons.

    Fenced code is illustration. Sample output and worked examples are not
    claims, and linting them produces noise that trains people to ignore the
    linter -- which costs more than the findings are worth.

    ``<!-- lint:off -->`` regions are the escape hatch for provenance and
    telemetry: token counts, run identifiers, cost estimates the engine emitted
    about itself. Those are facts about the run rather than claims about the
    world, and they have no source to cite because they *are* the source. The
    hatch is deliberately visible in the document and its use is counted in the
    report, because an unreported suppression is indistinguishable from a
    finding someone did not want to see.
    """
    out: list[tuple[int, str]] = []
    in_fence = False
    suppressed = 0
    off = False
    for n, line in enumerate(text.splitlines(), 1):
        if LINT_OFF.search(line):
            off = True
            continue
        if LINT_ON.search(line):
            off = False
            continue
        if CODE_FENCE.match(line):
            in_fence = not in_fence
            continue
        if off:
            suppressed += 1
            continue
        if not in_fence:
            out.append((n, line))
    return out, suppressed


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator(cells: Sequence[str]) -> bool:
    return all(set(c) <= set("-: ") for c in cells)


def _table_blocks(lines: Sequence[tuple[int, str]]) -> list[list[tuple[int, list[str]]]]:
    """Group contiguous table lines into blocks, dropping separator rows.

    Blocks are split on the first *non-table* line rather than on a gap in line
    numbers. An earlier version split on numbering gaps, which meant the
    markdown separator row -- discarded before the split -- broke every table
    into a header block and a body block, and the body block then lost its
    first data row to a header-skipping heuristic that no longer applied.
    """
    blocks: list[list[tuple[int, list[str]]]] = []
    current: list[tuple[int, list[str]]] = []
    for _, line in lines:
        if _is_table_line(line):
            cells = _cells(line)
            if not _is_separator(cells):
                current.append((_, cells))
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _number(cell: str) -> float | None:
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", cell.replace("$", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def lint_dossier(
    text: str,
    *,
    gate_ids: Sequence[str] = (),
    expected_headline: float | None = None,
    tolerance: float = 0.005,
) -> LintReport:
    """Run every rule over a compiled dossier and return the findings."""
    findings: list[Finding] = []
    lines, suppressed = _content_lines(text)

    # -- L01: a figure with nothing behind it -------------------------------
    for n, line in lines:
        if not FIGURE.search(line):
            continue
        if TAGGED.search(line) or SOURCE_HINT.search(line) or PRIMARY_SOURCE_RE.search(line):
            continue
        if line.strip().startswith(("#", ">")):
            continue
        findings.append(Finding(
            "unsourced-figure", BLOCKING, n,
            "a figure appears with no source and no REAL/EST tag", line))

    # -- L02: REAL asserted without a document ------------------------------
    # A grade is a property of a ledger row, so this rule only inspects rows: a
    # table line carrying REAL in a cell of its own. Matching the bare word
    # anywhere flagged every sentence that *discusses* the REAL grade, which
    # trained the reader to skim past the rule that matters most.
    for n, line in lines:
        if not _is_table_line(line):
            continue
        cells = _cells(line)
        if not any(c.strip().upper() == "REAL" for c in cells):
            continue
        if PRIMARY_SOURCE_RE.search(line) or DOCUMENT_HINT.search(line):
            continue
        findings.append(Finding(
            "real-without-document", BLOCKING, n,
            "a row is graded REAL but names no retrievable document or identifier. "
            "REAL is earned by a document, not by confidence.", line))

    # -- L03: totals that do not reconcile ----------------------------------
    findings.extend(_check_totals(lines))

    # -- L04: a declared gate with no recorded result -----------------------
    body = "\n".join(line for _, line in lines)
    for gate_id in gate_ids:
        pattern = re.compile(rf"\b{re.escape(gate_id)}\b[^\n]*")
        mentions = pattern.findall(body)
        if not mentions:
            findings.append(Finding(
                "gate-without-result", BLOCKING, 0,
                f"gate {gate_id} is declared in the spec but never appears in the dossier"))
        elif not any(GATE_RESULT.search(m) for m in mentions):
            findings.append(Finding(
                "gate-without-result", BLOCKING, 0,
                f"gate {gate_id} appears but records no result "
                f"(expected one of PASS / FAIL / PENDING / DEFUNDED)"))

    # -- L05: sourced-sounding prose ----------------------------------------
    for n, line in lines:
        m = WEASEL.search(line)
        if m:
            findings.append(Finding(
                "unattributed-authority", ADVISORY, n,
                f"{m.group(0)!r} attributes a claim to nobody in particular", line))

    # -- L06: the stated headline must match the computed one ---------------
    if expected_headline is not None:
        stated = HEADLINE.search(body)
        if not stated:
            findings.append(Finding(
                "headline-missing", BLOCKING, 0,
                "the dossier states no headline P(success)"))
        else:
            claimed = float(stated.group(1)) / 100.0
            if abs(claimed - expected_headline) > tolerance:
                findings.append(Finding(
                    "headline-mismatch", BLOCKING, 0,
                    f"dossier states P(success) = {claimed:.1%} but the ledger computes "
                    f"{expected_headline:.1%}. The narrative and the arithmetic disagree."))

    return LintReport(findings=tuple(findings), suppressed_lines=suppressed)


def _check_totals(lines: Sequence[tuple[int, str]]) -> list[Finding]:
    """A markdown table whose Total row disagrees with its own column.

    Narrow on purpose: it only fires on an explicit total, and only when every
    other row in that column parses as a number. A reconciliation check that
    guesses produces false positives, and a linter with false positives is a
    linter people learn to override.
    """
    findings: list[Finding] = []

    for block in _table_blocks(lines):
        total_rows = [(n, c) for n, c in block if re.match(r"^\**total\**$", c[0].strip(), re.I)]
        if not total_rows:
            continue
        total_n, total_cells = total_rows[0]
        rest = [c for n, c in block if not re.match(r"^\**total", c[0].strip(), re.I)]
        # The header is the first row whose cells carry no numbers at all.
        body = rest[1:] if rest and all(_number(c) is None for c in rest[0]) else rest
        if len(body) < 2:
            continue
        for col in range(1, min(len(total_cells), min((len(c) for c in body), default=0))):
            stated = _number(total_cells[col])
            parts = [_number(c[col]) for c in body]
            if stated is None or not parts or any(p is None for p in parts):
                continue
            actual = sum(p for p in parts if p is not None)
            if abs(actual - stated) > max(0.01, abs(stated) * 0.005):
                findings.append(Finding(
                    "unreconciled-total", BLOCKING, total_n,
                    f"column {col + 1} totals {stated:,.2f} but its rows sum to {actual:,.2f}",
                    " | ".join(total_cells)))
    return findings
