"""The command surface.

    run-engine run       --pack packs/manufacturing [--offline | --live]
    run-engine sources   --pack packs/manufacturing
    run-engine calibrate --pack packs/manufacturing
    run-engine amend     --pack packs/manufacturing {list,propose,vote,ratify}
    run-engine promote   <staged> <ledger> [--apply]
    run-engine packs

``--offline`` is the default everywhere, and ``--live`` is a deliberate opt-in,
because nothing should start spending money because someone forgot a flag.

``promote`` is the odd one out and stays that way on purpose: it is a human's
command, previews by default, and no agent is ever given a tool that calls it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .calibration import load_all, report as calibration_report, resolve
from .engine import RunOptions, run as run_engine
from .evidence.ledger import promote
from .pack import Pack, PackError, available_packs
from .runstate import load_runs
from .spec import propose, ratify, vote
from .spec.model import SpecError


def _load(args) -> Pack:
    return Pack.load(args.pack)


def _backend_for(args):
    """Build a backend, or explain precisely why we cannot."""
    if args.offline:
        from .agents.backend import OfflineBackend
        return OfflineBackend()
    from .agents.backends.anthropic import AnthropicBackend
    return AnthropicBackend()


def _router_for(args, pack: Pack):
    from .retrieval.router import Router
    if args.offline:
        from .testing import fake_adapters
        return Router(adapters=fake_adapters())
    from .retrieval.sources import default_adapters
    wired = set(pack.sources.adapters())
    adapters = {name: fn for name, fn in default_adapters().items() if name in wired} \
        if wired else default_adapters()
    return Router(adapters=adapters)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_run(args) -> int:
    pack = _load(args)
    backend = _backend_for(args)
    router = _router_for(args, pack)

    options = RunOptions(
        offline=args.offline,
        attack=args.attack,
        divergence=args.divergence,
        diverse=args.diverse,
        budget=args.budget,
        runs_dir=Path(args.runs_dir),
        evidence=Path(args.evidence) if args.evidence else _default_evidence(pack),
        staged=Path(args.staged) if args.staged else None,
    )
    result = run_engine(pack, options, backend=backend, router=router)

    print(f"run {result.run_id} · phase 0: {result.phase0}")
    print(result.estimate.line())
    print(f"master metric: {result.metric.line()}")
    print("gates: " + ", ".join(f"{o.id}={o.status}" for o in result.ladder.outcomes))
    if result.ladder.blocked:
        print(f"BLOCKED at {result.ladder.failed_at} — downstream defunded "
              f"({', '.join(result.ladder.defunded) or 'none'}), routed to "
              f"{result.ladder.routed_to}")
    print(f"lint: {len(result.lint.blocking)} blocking, {len(result.lint.advisory)} advisory")
    if result.continuity.plateau:
        print("PLATEAU: three flat runs. The constraint is money or access, not analysis.")
    print(f"archive: {result.root}")
    print(f"report:  {result.root / 'report.html'}")
    return 0


def _default_evidence(pack: Pack) -> Path | None:
    candidate = pack.root / "evidence.md"
    return candidate if candidate.is_file() else None


def cmd_sources(args) -> int:
    pack = _load(args)
    print(f"# Data layer — {pack.name}\n")
    print(pack.sources.table())
    print()
    print("The evidence class is a property of the source, not of the finding. An agent "
          "reports what it found and where; this table decides what that is worth.")
    return 0


def cmd_calibrate(args) -> int:
    pack = _load(args)
    runs_dir = Path(args.runs_dir)
    predictions = load_all(runs_dir)
    results: dict[str, str] = {}
    for record in reversed(load_runs(runs_dir)):
        results.update(record.get("gates") or {})
    print(calibration_report(resolve(predictions, results)))
    if not predictions:
        print(f"(no prediction logs under {runs_dir} — run `run-engine run --pack "
              f"{pack.root}` first)")
    return 0


def cmd_amend(args) -> int:
    pack = _load(args)
    spec = pack.spec

    if args.action == "list":
        if not spec.amendments:
            print("No amendments proposed. The spec stands as ratified.")
            return 0
        for a in spec.amendments:
            approve, reject = a.tally()
            print(f"{a.id}  [{a.status}]  {a.proposal}")
            print(f"    target {a.target} -> {a.value}   votes {approve}/{approve + reject}")
        return 0

    try:
        if args.action == "propose":
            amendment = propose(spec, args.proposal, args.target, args.value, by=args.by)
            print(f"{amendment.id} queued. It changes nothing until it is voted on and "
                  f"then independently ratified.")
        elif args.action == "vote":
            amendment = vote(spec, args.id, args.by, args.ballot)
            approve, reject = amendment.tally()
            print(f"{amendment.id}: {approve} approve / {reject} reject → {amendment.status}")
            if amendment.status == "approved":
                print("Approved. Still not applied — ratification is a separate act by a "
                      "person who neither proposed nor voted on it.")
        elif args.action == "ratify":
            ratify(spec, args.id, by=args.by)
            print(f"{args.id} ratified. Spec is now v{spec.version}.")
            print(spec.changelog[-1].diff or "(no textual diff)")
    except SpecError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    spec.save()
    return 0


def cmd_promote(args) -> int:
    report = promote(args.staged, args.ledger, apply=args.apply)
    print(report.render())
    if not args.apply:
        print("\nPreview only. Re-run with --apply to write.")
    return 0


def cmd_packs(args) -> int:
    found = available_packs(args.root)
    if not found:
        print(f"no packs under {args.root}")
        return 1
    for name in found:
        try:
            pack = Pack.load(Path(args.root) / name)
            print(f"{name:<16} v{pack.spec.version}  {pack.description or pack.brief.gain}")
        except PackError as exc:
            print(f"{name:<16} INVALID — {exc}")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pack", required=True, help="path to a pack directory")
    p.add_argument("--runs-dir", default="runs", help="where run archives are written")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="run-engine",
        description="A governed run engine: frozen by default, evidence-gated, auditable.")
    sub = ap.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="execute one governed run")
    _add_common(r)
    r.add_argument("--offline", action="store_true", default=True,
                   help="deterministic stub backend and fake sources (default)")
    r.add_argument("--live", dest="offline", action="store_false",
                   help="use a real model and real sources; requires credentials")
    r.add_argument("--attack", action="store_true",
                   help="commission a run whose only job is to kill the plan")
    r.add_argument("--divergence", action="store_true",
                   help="score alternatives against the same master metric")
    r.add_argument("--diverse", action="store_true",
                   help="route attacking seats to MODEL_OUTSIDE, so an attack does not "
                        "share its target's training blind spots")
    r.add_argument("--budget", default="lean", choices=["lean", "standard", "deep"])
    r.add_argument("--evidence", help="path to the authoritative ledger")
    r.add_argument("--staged", help="path agents may stage proposals to")
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("sources", help="show the data layer and what it needs")
    _add_common(s)
    s.set_defaults(func=cmd_sources)

    c = sub.add_parser("calibrate", help="Brier-score the predictions made so far")
    _add_common(c)
    c.set_defaults(func=cmd_calibrate)

    a = sub.add_parser("amend", help="propose, vote on, or ratify a spec change")
    _add_common(a)
    a.add_argument("action", choices=["list", "propose", "vote", "ratify"])
    a.add_argument("--id", help="amendment id, for vote and ratify")
    a.add_argument("--by", default="", help="who is acting (required for every act)")
    a.add_argument("--proposal", default="", help="what the change is, for propose")
    a.add_argument("--target", default="", help="dotted spec path, for propose")
    a.add_argument("--value", default="", help="new value, for propose")
    a.add_argument("--ballot", default="APPROVE", help="APPROVE / REJECT / AMEND, for vote")
    a.set_defaults(func=cmd_amend)

    p = sub.add_parser(
        "promote", help="move staged rows into the authoritative ledger (a human's command)")
    p.add_argument("staged")
    p.add_argument("ledger")
    p.add_argument("--apply", action="store_true", help="write; previews without it")
    p.set_defaults(func=cmd_promote)

    k = sub.add_parser("packs", help="list available instantiations")
    k.add_argument("--root", default="packs")
    k.set_defaults(func=cmd_packs)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except PackError as exc:
        print(f"pack error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the CLI is the boundary; report, don't traceback
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
