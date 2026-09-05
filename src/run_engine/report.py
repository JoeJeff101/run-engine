"""What a finished run hands back: a dossier of record, and a page you can send.

The dossier is markdown, because it has to survive being read in a terminal, on
GitHub, and printed to PDF by someone in a meeting. The HTML report is the same
information laid out for a reader who is deciding something -- a lender, an
investment committee, a collaborator -- and is self-contained so it can be
emailed as one file.

Both are generated from the same run data, and both are held to the same
standard as everything else the engine emits: every figure carries a tag or a
source, because the linter that checks the dossier is the linter that checks
this module's output too. Exempting our own writing from our own rule would be
the most obvious possible failure of the whole exercise.
"""

from __future__ import annotations

import html as _html
from typing import Any, Sequence

from .evidence.ledger import Row
from .pack import Pack

FREEZE_NOTE = {
    "frozen": "Frozen to the incumbent hypothesis. This run refines the plan; it does not "
              "reopen it. Exploration is a privilege that new REAL evidence buys.",
    "thawed": "**Thawed by new evidence.** A newly promoted REAL row has earned this run "
              "the right to reconsider the model.",
    "attack": "**Attack run.** Commissioned explicitly to find the assumption that ends "
              "the venture. Its findings are in `00b_attack.md`.",
    "divergence": "**Divergence run.** Alternatives were scored against the same master "
                  "metric. Findings are in `00a_divergence.md`, and become proposed "
                  "amendments rather than an instant pivot.",
}


# ---------------------------------------------------------------------------
# Markdown dossier
# ---------------------------------------------------------------------------


def dossier(pack: Pack, run_id: str, ladder, est, metric, board_run,
            *, rows: Sequence[Row] = (), phase0: str = "frozen") -> str:
    reals = [r for r in rows if r.grade.upper() == "REAL"]
    lines: list[str] = [
        f"# {pack.name} — technical dossier",
        "",
        "<!-- lint:off -->",
        f"Run `{run_id}` · spec v{pack.spec.version} · {len(pack.board)} seats · "
        f"{len(pack.tasks)} tasks",
        "<!-- lint:on -->",
        "",
        "The compiled artifact this run is judged on. If it is not in here, it did not "
        "happen.",
        "",
        "## Brief",
        "",
        f"> {pack.brief.one_line()}",
        "",
        "Two clauses. The second is the one that makes success distinguishable from ruin.",
        "",
        "## Phase 0",
        "",
        FREEZE_NOTE.get(phase0, phase0),
        "",
        "## Master metric",
        "",
        f"**{pack.spec.master_metric.one_line()}** "
        f"(source: spec v{pack.spec.version}, ratified)",
        "",
        f"Score this run: {metric.line()} [EST]",
        "",
        "## Headline",
        "",
        f"**{est.line()}** [EST]",
        "",
        est.caveat(),
        "",
        "The chain is a product, not an average: each gate is conditional on the ones "
        "before it. Only rows graded REAL move any term.",
        "",
    ]

    lines += [ladder.to_markdown(), ""]

    lines += ["## Substrate", "",
              "The state machine this plan acts on. The final row is the key — the "
              "deliberate mechanism that undoes the locked state. Without it the lock "
              "would be a trap.", "",
              "| Stage | In this domain |", "|---|---|"]
    for step in pack.substrate.steps:
        lines.append(f"| {step.stage} | {step.name} |")
    lines.append("")

    lines += ["## The crux", "",
              f"**{pack.tasks.crux.id} — {pack.tasks.crux.title}.** "
              f"{pack.tasks.crux.description or 'The single question that makes everything '
                                                'else moot.'}",
              "",
              "## Evidence", ""]
    if not rows:
        lines += [
            "The ledger is empty. Every number in this document is therefore an "
            "assumption, tagged EST, and the headline above rests entirely on the "
            "priors written into the spec.",
            "",
            "This is the correct output for a plan nobody has tested yet. It is not "
            "dressed up as anything better.",
            "",
        ]
    else:
        lines += [f"{len(rows)} row(s), {len(reals)} of them REAL.", "",
                  "| Row | Topic | Claim | Value | Grade | Source |", "|---|---|---|---|---|---|"]
        for r in rows[:40]:
            lines.append(f"| {r.id or '—'} | {r.topic} | {r.claim} | {r.value} | {r.grade} | "
                         f"{r.source} |")
        lines.append("")

    lines += ["## Board", "",
              f"{len(pack.board)} seats across {len(pack.board.phases)} phases. Quorum is "
              f"counted in disciplines, not heads: "
              f"{'quorate' if pack.board.is_quorate() else 'NOT QUORATE'}.", ""]
    for result in board_run.results:
        summary = " ".join((result.output or "").split())[:280]
        lines.append(f"- **{result.title}** ({result.phase}) — {summary or '_no output_'}")
    if board_run.usage_report:
        # Telemetry about this run, not a claim about the world. Suppressed
        # explicitly and counted in the lint report rather than quietly excused.
        lines += ["", "<!-- lint:off -->", f"_{board_run.usage_report}_",
                  "<!-- lint:on -->", ""]
    else:
        lines += ["", ""]

    lines += ["## What would change this", "",
              "See `95_value_of_information.md` for the ranked experiment queue. The "
              "top-ranked item collapses more uncertainty per unit spent than anything "
              "else available, which is why it is the one to fund.", ""]

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------


def _e(text: Any) -> str:
    return _html.escape(str(text), quote=True)


def _bar(fraction: float) -> str:
    pct = max(0.0, min(1.0, fraction)) * 100
    return (f'<div class="bar"><span style="width:{pct:.1f}%"></span></div>')


STYLE = """
:root{--paper:#fbfaf7;--ink:#161a19;--ink2:#3c4643;--mid:#5f6a66;--faint:#8b9591;
--rule:#dfe4e0;--card:#fff;--cold:#1d5f74;--warm:#a8412a;--good:#1f6f3f;--brass:#7a6214;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
--sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
--paper:#0f1312;--ink:#e6eae7;--ink2:#c2cac6;--mid:#939d99;--faint:#6d7773;
--rule:#28302d;--card:#161b19;--cold:#6fc0d6;--warm:#ec8a6e;--good:#5fbe86;--brass:#c9aa53}}
:root[data-theme=dark]{--paper:#0f1312;--ink:#e6eae7;--ink2:#c2cac6;--mid:#939d99;
--faint:#6d7773;--rule:#28302d;--card:#161b19;--cold:#6fc0d6;--warm:#ec8a6e;
--good:#5fbe86;--brass:#c9aa53}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);
font-size:15.5px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:980px;margin:0 auto;padding:40px 24px 80px}
header{border-bottom:2px solid var(--ink);padding-bottom:18px;margin-bottom:28px}
.kicker{font-family:var(--mono);font-size:11px;letter-spacing:.15em;text-transform:uppercase;
color:var(--cold);margin:0 0 10px}
h1{font-size:clamp(26px,4vw,38px);line-height:1.1;margin:0 0 10px;letter-spacing:-.02em}
.deck{color:var(--ink2);margin:0 0 16px;max-width:62ch}
.meta{display:flex;flex-wrap:wrap;gap:0 28px;font-family:var(--mono);font-size:11.5px;
color:var(--mid);border-top:1px solid var(--rule);padding-top:12px}
.meta b{display:block;color:var(--ink);font-weight:500}
.meta span{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
h2{font-size:20px;margin:36px 0 4px;letter-spacing:-.01em}
h2+p.sub{color:var(--mid);margin:0 0 14px;font-size:14px}
.hero{background:var(--card);border:1px solid var(--rule);border-left:3px solid var(--cold);
border-radius:3px;padding:22px 24px;margin:20px 0}
.figure{font-family:var(--mono);font-size:clamp(30px,6vw,46px);font-weight:600;
letter-spacing:-.02em;line-height:1;margin:0 0 6px}
.figsub{font-family:var(--mono);font-size:12.5px;color:var(--mid);margin:0}
.bar{height:7px;background:var(--rule);border-radius:4px;overflow:hidden;margin:14px 0 6px}
.bar span{display:block;height:100%;background:var(--cold)}
.caveat{border-left:3px solid var(--warm);background:color-mix(in srgb,var(--warm) 8%,transparent);
padding:12px 16px;margin:16px 0;font-size:14.5px;border-radius:0 3px 3px 0}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:14px 0}
.scroll{overflow-x:auto;border:1px solid var(--rule);border-radius:3px;background:var(--card)}
th{text-align:left;font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;
color:var(--mid);padding:10px 12px;border-bottom:1px solid var(--ink);white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid var(--rule);color:var(--ink2);vertical-align:top}
tr:last-child td{border-bottom:none}
td.num{font-family:var(--mono);text-align:right;white-space:nowrap}
td.id{font-family:var(--mono);font-weight:500;color:var(--ink);white-space:nowrap}
.pill{display:inline-block;font-family:var(--mono);font-size:9.5px;letter-spacing:.07em;
text-transform:uppercase;padding:2px 7px;border-radius:2px;border:1px solid currentColor}
.p-pass{color:var(--good)}.p-fail{color:var(--warm)}.p-pending{color:var(--mid)}
.p-defunded{color:var(--warm);opacity:.75}.p-real{color:var(--cold)}.p-est{color:var(--brass)}
ul.items{padding-left:18px;margin:10px 0}ul.items li{margin-bottom:8px;color:var(--ink2)}
.note{background:var(--card);border:1px solid var(--rule);border-radius:3px;
padding:14px 18px;margin:16px 0;font-size:14.5px;color:var(--ink2)}
footer{border-top:1px solid var(--rule);margin-top:48px;padding-top:16px;
font-family:var(--mono);font-size:11.5px;color:var(--faint);line-height:1.7}
code{font-family:var(--mono);font-size:.88em}
"""


def html(pack: Pack, run_id: str, ladder, est, metric, lint_report, continuity, ranked,
         *, phase0: str = "frozen", started: str = "") -> str:
    """A self-contained page. One file, no external assets, readable in either theme."""
    gate_rows = "\n".join(
        f'<tr><td class="id">{_e(o.id)}</td><td class="num">{o.gate.cost:,.0f}</td>'
        f'<td><span class="pill p-est">EST</span></td>'
        f'<td><span class="pill p-{o.status}">{_e(o.status)}</span></td>'
        f"<td>{_e(o.gate.question)}</td></tr>"
        for o in ladder.outcomes)

    term_rows = "\n".join(
        f'<tr><td class="id">{_e(t.term)}</td>'
        f'<td class="num">{t.posterior.mean:.3f}</td>'
        f'<td class="num">{t.real_observations:,.0f}</td>'
        f'<td class="num">{t.prior_mass:,.1f}</td></tr>'
        for t in est.terms)

    voi_rows = "\n".join(
        f'<tr><td class="id">{_e(r.experiment.id)}</td><td>{_e(r.experiment.label)}</td>'
        f'<td class="id">{_e(r.experiment.term)}</td>'
        f'<td class="num">{r.experiment.cost:,.0f}</td>'
        f'<td class="num">{r.value_per_cost:.2e}</td></tr>'
        for r in ranked[:8]) or '<tr><td colspan="5">No experiments queued.</td></tr>'

    items = "\n".join(
        f"<li><b>{_e(i.id)}</b> — {_e(i.question)}"
        + (f"<br><span style='color:var(--faint)'>blocked on: {_e(i.blocked_on)}</span>"
           if i.blocked_on else "") + "</li>"
        for i in continuity.unresolved()) or "<li>Nothing carried forward.</li>"

    lint_line = (
        f"{len(lint_report.blocking)} blocking, {len(lint_report.advisory)} advisory"
        if lint_report.findings else "clean — every figure carries a source")

    plateau_block = (
        '<div class="caveat"><b>Plateau.</b> The last three runs did not move the headline '
        'and the open items did not shrink. The constraint is money or access, not '
        'analysis: fund the top-ranked experiment or accept the current estimate.</div>'
        if continuity.plateau else "")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(pack.name)} — run {_e(run_id)}</title>
<style>{STYLE}</style></head><body>
<div class="wrap">

<header>
  <p class="kicker">Run engine · governed run</p>
  <h1>{_e(pack.name)}</h1>
  <p class="deck">{_e(pack.brief.one_line())}</p>
  <div class="meta">
    <div><span>Run</span><b>{_e(run_id)}</b></div>
    <div><span>Spec</span><b>v{pack.spec.version}</b></div>
    <div><span>Phase 0</span><b>{_e(phase0)}</b></div>
    <div><span>Started</span><b>{_e(started or '—')}</b></div>
    <div><span>Lint</span><b>{_e(lint_line)}</b></div>
  </div>
</header>

<h2>Headline</h2>
<p class="sub">Three figures, never one. A mean alone invites being quoted alone.</p>
<div class="hero">
  <p class="figure">{est.mean:.1%}</p>
  <p class="figsub">80% credible interval {est.lo:.1%} – {est.hi:.1%}
     · REAL fraction {est.real_fraction:.2f}</p>
  {_bar(est.real_fraction)}
  <p class="figsub">Share of this estimate resting on promoted evidence rather than priors.</p>
</div>
<div class="caveat">{_e(est.caveat())}</div>
{plateau_block}

<h2>Master metric</h2>
<p class="sub">One scalar, two halves. A performance target without a survival
constraint produces plans that win and kill the company.</p>
<div class="note"><b>{_e(pack.spec.master_metric.performance)}</b>,
with <b>{_e(pack.spec.master_metric.do_no_harm)}</b>.<br>
Score this run: <code>{_e(metric.line())}</code></div>

<h2>Gate ladder</h2>
<p class="sub">Ordered by cost of falsification, cheapest first. A red gate defunds
everything after it.</p>
<div class="scroll"><table>
<thead><tr><th>Gate</th><th>Cost to falsify</th><th>Basis</th><th>Status</th><th>Question</th></tr></thead>
<tbody>{gate_rows}</tbody></table></div>
{'<div class="caveat"><b>' + _e(str(ladder.failed_at)) + ' failed.</b> Downstream work is defunded ('
 + _e(", ".join(ladder.defunded)) + '). The run routes back to ' + _e(ladder.routed_to)
 + ' to rework the mechanism. Do not fund tooling, inventory, hiring or marketing against '
   'this plan until it re-enters the ladder and clears.</div>' if ladder.blocked else ''}

<h2>Where the number comes from</h2>
<p class="sub">Each term is a Beta posterior. Only REAL rows contribute observations;
prior mass is what the spec assumed before anyone looked.</p>
<div class="scroll"><table>
<thead><tr><th>Term</th><th>Posterior mean</th><th>REAL observations</th><th>Prior mass</th></tr></thead>
<tbody>{term_rows}</tbody></table></div>

<h2>What to fund next</h2>
<p class="sub">Ranked by variance reduction per unit cost. The ordering falls out of the
arithmetic rather than being imposed on it.</p>
<div class="scroll"><table>
<thead><tr><th>Experiment</th><th>Label</th><th>Informs</th><th>Cost</th><th>VoI / cost</th></tr></thead>
<tbody>{voi_rows}</tbody></table></div>

<h2>Open items</h2>
<p class="sub">What this run could not settle, carried into the next one.</p>
<ul class="items">{items}</ul>

<footer>
Generated by run-engine from run <code>{_e(run_id)}</code> against spec v{pack.spec.version}.<br>
The full archive — grounding, task notes, red-team verdict, lint report, ledger snapshot
and prediction log — is in the run folder alongside this file.<br>
Only rows graded REAL moved the number above.
</footer>

</div></body></html>
"""
