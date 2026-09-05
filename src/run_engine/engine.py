"""The run loop: one full turn of the crank.

    ground -> phase 0 -> votes -> board -> finalize -> dossier
           -> red team -> lint -> value of info -> ledger -> continuity

Two properties of this loop matter more than its steps.

**Frozen by default.** Phase 0 defaults to *no*. The system refines the
incumbent hypothesis and will not entertain alternatives unless someone passes
an explicit flag or new REAL evidence lands. Exploration is a privilege that
evidence buys. For anyone who has watched a plan get reopened because somebody
read something on a Tuesday, this is the single most valuable import in the
whole design: it makes infinite pivoting on zero new information structurally
impossible.

**Re-grounded every run.** Every agent is reloaded from the brief, the current
spec, its persona and the ledger. Nothing works from memory and nothing carries
a session forward, so "as we discussed last month" cannot enter a document. The
cost is that grounding is rebuilt every time; the benefit is that a run is
reproducible from its artifacts alone, which is the only reason the archive is
worth keeping.

On gate results
---------------
A gate passes when a REAL row says it passed. Not when the board believes it
did, and not when the model is confident. A fresh pack therefore reports every
gate PENDING and a probability resting entirely on priors -- which is the
correct output for a plan nobody has tested yet, and is deliberately not
dressed up as anything better.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import report as reporting
from .agents.backend import LLMBackend, OfflineBackend
from .agents.sequential import run_board
from .agents.tournament import Option, red_team, run_tournament
from .calibration import PREDICTION_LOG, record_predictions
from .evidence.ledger import Row, StagingLedger, _parse_table  # noqa: F401
from .gates import Ladder, score_master_metric
from .lint import lint_dossier
from .pack import Pack
from .probability import MARKET, estimate, observation, priors_from_spec
from .runstate import (
    Continuity, OpenItem, RunFolder, detect_plateau, history_for_plateau, latest_continuity,
    load_runs,
)
from .voi import rank, to_markdown as voi_markdown, unranked_terms

FROZEN, THAWED, ATTACK, DIVERGENCE = "frozen", "thawed", "attack", "divergence"


class EngineError(RuntimeError):
    """Raised when a run cannot be started."""


@dataclass
class RunOptions:
    offline: bool = True
    attack: bool = False
    divergence: bool = False
    budget: str = "lean"
    runs_dir: Path = Path("runs")
    evidence: Path | None = None      # the authoritative ledger, if the pack has one
    staged: Path | None = None        # where agents may stage proposals
    run_id: str | None = None
    at: datetime | None = None


@dataclass
class RunResult:
    folder: RunFolder
    run_id: str
    phase0: str
    estimate: Any
    ladder: Any
    lint: Any
    continuity: Continuity
    metric: Any
    ranked: list = field(default_factory=list)

    @property
    def root(self) -> Path:
        return self.folder.root


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def load_evidence(path: Path | None) -> list[Row]:
    """Read the authoritative ledger. A pack with none simply has no evidence yet."""
    if path is None or not Path(path).is_file():
        return []
    rows: list[Row] = []
    for raw in _parse_table(Path(path)):
        rows.append(Row(
            topic=raw.get("topic", ""), claim=raw.get("claim", ""), value=raw.get("value", ""),
            grade=raw.get("grade", ""), source=raw.get("source", ""),
            origin=raw.get("origin", ""), run=raw.get("run", ""), id=raw.get("id", ""),
        ))
    return rows


def gate_results(pack: Pack, rows: list[Row]) -> dict[str, str]:
    """A gate's status, read off the evidence rather than off the argument.

    Only a REAL row explicitly recording pass or fail decides a gate. Ratio rows
    ("45/50") inform the probability but do not by themselves declare a verdict,
    because deciding where the threshold sits is what the written pass condition
    is for and that is a human's call.
    """
    results: dict[str, str] = {}
    for gate in pack.spec.gates:
        for row in rows:
            if row.topic.upper() != gate.id.upper() or row.grade.upper() != "REAL":
                continue
            obs = observation(row.value)
            if obs == (1.0, 0.0):
                results[gate.id] = "pass"
            elif obs == (0.0, 1.0):
                results[gate.id] = "fail"
    return results


def real_row_count(rows: list[Row]) -> int:
    return sum(1 for r in rows if r.grade.upper() == "REAL")


# ---------------------------------------------------------------------------
# Phase 0 -- the freeze
# ---------------------------------------------------------------------------


def resolve_phase0(options: RunOptions, *, real_rows: int, previous: list[dict[str, Any]]) -> str:
    """Decide whether this run may explore. Defaults to no.

    Three things unfreeze it, and exactly three: an explicit ``--attack`` flag,
    an explicit ``--divergence`` flag, or a newly promoted REAL row. That last
    one is the only automatic path, and it is the point -- a signed quote at a
    surprising number is what earns you the right to reconsider the model.
    """
    if options.attack:
        return ATTACK
    if options.divergence:
        return DIVERGENCE
    if previous:
        before = int(previous[0].get("real_rows", 0) or 0)
        if real_rows > before:
            return THAWED
    return FROZEN


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


def run(pack: Pack, options: RunOptions | None = None, *,
        backend: LLMBackend | None = None, router: Any = None) -> RunResult:
    """Execute one governed run and return its sealed archive."""
    options = options or RunOptions()

    if backend is None:
        if not options.offline:
            raise EngineError(
                "a live run needs a backend; construct one and pass it in, or use "
                "run_engine.cli which will build it from the environment")
        backend = OfflineBackend()

    if router is None and options.offline:
        from .retrieval.router import Router
        from .testing import fake_adapters
        router = Router(adapters=fake_adapters())

    previous = load_runs(options.runs_dir)
    rows = load_evidence(options.evidence)
    reals = real_row_count(rows)

    folder = RunFolder.create(options.runs_dir, run_id=options.run_id, at=options.at)
    started = (options.at or datetime.now(timezone.utc)).isoformat(timespec="seconds")

    # -- 1. ground ----------------------------------------------------------
    carried = latest_continuity(options.runs_dir)
    ledger_digest = _ledger_digest(rows)
    folder.write("00_grounding.md", pack.grounding(
        ledger_digest=ledger_digest,
        continuity=carried.to_markdown("previous") if previous else "",
    ))

    # -- 2. phase 0: may this run explore? ----------------------------------
    phase0 = resolve_phase0(options, real_rows=reals, previous=previous)
    if phase0 == DIVERGENCE:
        folder.write("00a_divergence.md", _divergence(pack, backend))
    elif phase0 == ATTACK:
        folder.write("00b_attack.md", _attack(pack, backend))

    # -- 3. pending amendments ---------------------------------------------
    folder.write("00c_amendments.md", _amendments(pack))

    # -- 4-5. the board and the work chain ---------------------------------
    staging = StagingLedger(options.staged, run_id=folder.run_id) if options.staged else None
    board_run = run_board(pack.board, backend, router=router, budget=options.budget,
                          staging=staging)
    by_seat = board_run.by_key()
    for task in pack.tasks:
        result = by_seat.get(task.seat)
        folder.write(f"{task.id}_{_slug(task.title)}.md",
                     _task_note(pack, task, result))

    # -- 6. predictions, logged BEFORE the gates are evaluated --------------
    priors = priors_from_spec(pack.spec)
    prior_only = estimate(priors, [])
    record_predictions(
        folder,
        {t.term: t.posterior.mean for t in prior_only.terms},
        run_id=folder.run_id,
        at=started,
    )

    # -- 7-8. gates and arithmetic -----------------------------------------
    ladder = Ladder.from_spec(pack.spec, lock_task=pack.lock_task)
    results = gate_results(pack, rows)
    outcome = ladder.evaluate(results)
    est = estimate(priors, rows)

    do_no_harm_held = outcome.failed_at is None
    metric = score_master_metric(outcome, do_no_harm_held=do_no_harm_held)

    # -- 9. the dossier -----------------------------------------------------
    dossier = reporting.dossier(pack, folder.run_id, outcome, est, metric, board_run,
                                rows=rows, phase0=phase0)
    folder.write("99_dossier.md", dossier)

    # -- 10. red team, before the linter -----------------------------------
    verdict = red_team(
        claim=f"{pack.brief.one_line()} — {est.line()}",
        backend=backend,
        context=pack.spec.digest(),
    )
    folder.write("97_redteam_verdict.md", _verdict_note(verdict, est))

    # -- 11. lint -----------------------------------------------------------
    report = lint_dossier(dossier, gate_ids=pack.gate_ids(), expected_headline=est.mean)
    folder.write("98_lint_report.md", report.to_markdown())
    metric = score_master_metric(outcome, do_no_harm_held=do_no_harm_held,
                                 lint_blocking=len(report.blocking))

    # -- 12. value of information + promotion candidates --------------------
    posteriors = {t.term: t.posterior for t in est.terms}
    ranked = rank(pack.experiments, posteriors)
    folder.write("95_value_of_information.md", voi_markdown(
        ranked, unfunded=unranked_terms(pack.experiments, posteriors)))
    folder.write("96_promotion_candidates.md", _promotion_candidates(rows, options))

    # -- 13. ledger snapshot ------------------------------------------------
    folder.write("_ledger_snapshot.tsv", _snapshot(rows))

    # -- 14. continuity + the plateau flag ----------------------------------
    open_items = _open_items(pack, outcome, report, folder.run_id)
    history = [(est.mean, len(open_items))] + history_for_plateau(previous)
    plateau = detect_plateau(history)
    continuity = Continuity(open_items=open_items, headline=est.mean, plateau=plateau)
    folder.write("_continuity.md", continuity.to_markdown(folder.run_id))

    # -- 15-16. machine-readable record and the shareable report ------------
    folder.write_json("run.json", {
        "run_id": folder.run_id,
        "pack": pack.name,
        "spec_version": pack.spec.version,
        "started_at": started,
        "phase0": phase0,
        "offline": options.offline,
        "probability": est.to_dict(),
        "master_metric": metric.value,
        "gates": {o.id: o.status for o in outcome.outcomes},
        "failed_at": outcome.failed_at,
        "routed_to": outcome.routed_to,
        "real_rows": reals,
        "lint_blocking": len(report.blocking),
        "plateau": plateau,
        "open_items": [i.to_dict() for i in open_items],
        "top_experiment": ranked[0].experiment.id if ranked else None,
    })
    folder.write("report.html", reporting.html(
        pack, folder.run_id, outcome, est, metric, report, continuity, ranked,
        phase0=phase0, started=started))

    folder.seal()
    return RunResult(folder=folder, run_id=folder.run_id, phase0=phase0, estimate=est,
                     ladder=outcome, lint=report, continuity=continuity, metric=metric,
                     ranked=ranked)


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    return "_".join("".join(c if c.isalnum() else " " for c in text.lower()).split())[:40]


def _ledger_digest(rows: list[Row]) -> str:
    if not rows:
        return ("The ledger is empty. Nothing is established yet, so every number in "
                "this run is an assumption and must be tagged as one.")
    reals = [r for r in rows if r.grade.upper() == "REAL"]
    lines = [f"{len(rows)} row(s), of which {len(reals)} are REAL."]
    for row in rows[:20]:
        lines.append(f"  [{row.grade}] {row.topic}: {row.claim} = {row.value} ({row.source})")
    return "\n".join(lines)


def _amendments(pack: Pack) -> str:
    pending = pack.spec.pending()
    lines = [f"# Pending amendments — spec v{pack.spec.version}", ""]
    if not pending:
        lines.append("None. The spec stands as ratified; this run refines it rather than "
                     "reopening it.")
        return "\n".join(lines) + "\n"
    lines.append("Queued for the board. A change lands only after a vote *and* an "
                 "independent ratification.")
    lines.append("")
    for a in pending:
        approve, reject = a.tally()
        lines.append(f"- **{a.id}** ({a.status}) — {a.proposal}")
        lines.append(f"  - target: `{a.target}` → `{a.value}`  ·  votes: {approve} approve / "
                     f"{reject} reject")
    return "\n".join(lines) + "\n"


def _divergence(pack: Pack, backend: LLMBackend) -> str:
    """Alternatives compete under identical rules, scored on the same metric."""
    try:
        options = [
            Option(key=f"alt{i}",
                   description=f"Alternative route to: {pack.brief.gain} (framing {i})")
            for i in range(1, 4)
        ]
        result = run_tournament(
            question=f"Which route best serves the brief, judged on the master metric?",
            options=options, backend=backend, context=pack.spec.digest())
        body = result.transcript() if hasattr(result, "transcript") else str(result)
    except Exception as exc:  # a divergence branch must never fail the run
        body = f"_Tournament unavailable: {type(exc).__name__}: {exc}_"
    return ("# Divergence phase\n\n"
            "Invoked explicitly with `--divergence`. Alternatives are scored on the same "
            "master metric as the incumbent, so the comparison is honest, and any finding "
            "here becomes a *proposed amendment* rather than an instant pivot.\n\n"
            f"{body}\n")


def _attack(pack: Pack, backend: LLMBackend) -> str:
    """A run whose only job is to kill the plan."""
    verdict = red_team(
        claim=pack.brief.one_line(), backend=backend, context=pack.spec.digest(), voters=3)
    return ("# Attack run\n\n"
            "Invoked explicitly with `--attack`. The only job here is to find the "
            "assumption that ends the venture. This lands in its own file so it cannot be "
            "quietly merged into the optimistic case.\n\n"
            f"- refutations: {verdict.get('refuted', 0)} of {verdict.get('voters', 0)}\n"
            f"- survives: **{verdict.get('survives', False)}**\n\n"
            + "\n".join(f"> {n}" for n in verdict.get("notes", [])) + "\n")


def _task_note(pack: Pack, task, result) -> str:
    flags = [n for n, on in (("CRUX", task.crux), ("core", task.core),
                             ("FINALIZE", task.finalize)) if on]
    lines = [f"# {task.id} — {task.title}", ""]
    if flags:
        lines += [f"**{' · '.join(flags)}**", ""]
    lines += [f"Phase {task.phase} · owned by `{task.seat}`", ""]
    if task.description:
        lines += [task.description, ""]
    if task.crux:
        lines += ["> This is the crux. If it fails, everything downstream is worthless, "
                  "which is why it is tested first rather than discovered in month five.", ""]
    if task.finalize:
        lines += ["> This is the finalize step. It is run by a seat that owns no other "
                  "task in the chain — separation of duties is the point.", ""]
    if result is None:
        lines += ["_No board output for this seat in this run._"]
    else:
        lines += ["## Output", "", result.output or "_empty_"]
        if result.sources_used:
            lines += ["", f"_Sources consulted: {', '.join(result.sources_used)}_"]
        if result.error:
            lines += ["", f"**Error:** {result.error}"]
    return "\n".join(lines) + "\n"


def _verdict_note(verdict: dict[str, Any], est) -> str:
    survives = verdict.get("survives", False)
    lines = [
        "# Red-team verdict", "",
        "Adversarial review of the compiled dossier, run *before* mechanical validation, "
        "because a structurally clean plan can still be wrong.", "",
        f"- refutation attempts: **{verdict.get('voters', 0)}**",
        f"- successful refutations: **{verdict.get('refuted', 0)}**",
        f"- verdict: **{'survives' if survives else 'refuted'}** "
        f"(a claim survives on a majority of failed refutations; ties go against it)",
        "",
        f"Claim under attack: {est.line()}", "",
    ]
    notes = verdict.get("notes") or []
    if notes:
        lines += ["## Attempts", ""] + [f"{i}. {n}" for i, n in enumerate(notes, 1)]
    return "\n".join(lines) + "\n"


def _promotion_candidates(rows: list[Row], options: RunOptions) -> str:
    staged = [r for r in rows if r.grade.upper() == "EST"]
    lines = [
        "# Promotion candidates", "",
        "Rows that would become REAL if someone attached a document. Promotion is a "
        "separate command a person runs — no agent has a tool that calls it.", "",
    ]
    if not staged:
        lines.append("_Nothing staged is waiting on a document._")
    else:
        lines += ["| Row | Claim | Value | What would promote it |", "|---|---|---|---|"]
        for r in staged:
            lines.append(f"| {r.id or '—'} | {r.claim} | {r.value} | a retrievable primary "
                         f"document or authoritative identifier |")
    lines += ["", "```", f"run-engine promote {options.staged or '<staged>'} "
              f"{options.evidence or '<ledger>'} --apply", "```"]
    return "\n".join(lines) + "\n"


def _snapshot(rows: list[Row]) -> str:
    header = "id\ttopic\tclaim\tvalue\tgrade\tsource\torigin\trun"
    body = "\n".join(
        "\t".join([r.id, r.topic, r.claim, r.value, r.grade, r.source, r.origin, r.run])
        for r in rows)
    return header + ("\n" + body if body else "") + "\n"


def _open_items(pack: Pack, outcome, report, run_id: str) -> list[OpenItem]:
    """What this run could not settle. Derived, not narrated."""
    items: list[OpenItem] = []
    for o in outcome.outcomes:
        if o.status == "pending":
            items.append(OpenItem(
                id=f"OI-{o.id}", question=o.gate.question,
                blocked_on=f"a REAL row recording the result of {o.id} "
                           f"(pass condition: {o.gate.pass_condition})",
                opened_run=run_id))
    for finding in report.blocking:
        items.append(OpenItem(
            id=f"OI-LINT-{finding.rule}", question=finding.message,
            blocked_on="a source, a document, or a corrected total", opened_run=run_id))
    return items
