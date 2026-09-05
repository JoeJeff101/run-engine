"""CLI: execute a board and print the handoff trace.

    python -m evidence_pipeline.agents.run --board boards/example_board.yaml --offline

``--offline`` is the default and runs with no credentials and no network, so a
fresh clone works immediately. ``--live`` is a deliberate opt-in that requires a
configured backend, because nothing should start spending money because someone
forgot a flag.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..evidence.ledger import StagingLedger
from ..retrieval.router import Router
from ..testing import fake_adapters
from .backend import OfflineBackend
from .board import BoardError, load_board
from .sequential import run_board
from .tournament import Option, red_team, run_tournament


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a research board.")
    ap.add_argument("--board", default="boards/example_board.yaml", help="path to a board YAML")
    ap.add_argument("--offline", action="store_true", default=True,
                    help="deterministic stub backend and fake sources (default)")
    ap.add_argument("--live", dest="offline", action="store_false",
                    help="use real sources; requires configuration")
    ap.add_argument("--budget", default="lean", choices=["lean", "standard", "deep"])
    ap.add_argument("--stage", metavar="PATH", help="stage retrieved evidence to this file")
    ap.add_argument("--tournament", action="store_true", help="also run a tournament demo")
    ap.add_argument("--transcript", metavar="PATH", help="write the full transcript here")
    args = ap.parse_args(argv)

    try:
        board = load_board(args.board)
    except BoardError as exc:
        print(f"board error: {exc}", file=sys.stderr)
        return 2

    backend = OfflineBackend() if args.offline else None
    if backend is None:
        print(
            "--live requires a configured backend. This repository ships only the "
            "offline stub; implement agents.backend.LLMBackend against your provider "
            "and pass it to run_board().",
            file=sys.stderr,
        )
        return 2

    router = Router(adapters=fake_adapters()) if args.offline else Router()
    staging = StagingLedger(args.stage, run_id="demo") if args.stage else None

    mode = "offline (stub backend, fake sources)" if args.offline else "live"
    print(f"board:   {board.name}  [{len(board)} seats across {len(board.phases)} phases]")
    print(f"subject: {board.subject.strip()}")
    print(f"mode:    {mode}")
    print(f"budget:  {args.budget}\n")

    def trace(result) -> None:
        marker = "!" if result.error else "-"
        srcs = f" <- {', '.join(result.sources_used)}" if result.sources_used else ""
        print(f"  {marker} [{result.phase:<8}] {result.title:<28} ({result.tier}){srcs}")

    run = run_board(
        board=board,
        backend=backend,
        router=router,
        budget=args.budget,
        staging=staging,
        on_seat=trace,
    )

    print(f"\nseats executed: {len(run.results)}/{len(board)}")
    print(f"model usage:    {run.usage_report}")
    print(f"retrieval:      {router.savings_report()}")
    if staging:
        print(f"staged rows:    {len(run.staged)} -> {args.stage} (staged only; promotion is manual)")

    if args.tournament:
        print("\n--- tournament ---")
        options = [
            Option("approach-a", "A conventional, well-evidenced approach."),
            Option("approach-b", "A newer approach with a thinner evidence base."),
            Option("approach-c", "A speculative approach with high upside."),
        ]
        result = run_tournament(
            question="Which approach best fits the board's subject?",
            options=options,
            backend=backend,
        )
        print(result.render())

        incumbent = result.incumbent()
        if incumbent:
            print("\n--- adversarial verification of the incumbent ---")
            verdict = red_team(
                claim=f"{incumbent.option} is the best-supported approach.",
                backend=backend,
                voters=3,
            )
            outcome = "SURVIVES" if verdict["survives"] else "REFUTED"
            print(f"  {outcome}: {verdict['refuted']}/{verdict['voters']} reviewers refuted it")

    if args.transcript:
        Path(args.transcript).write_text(run.transcript(), encoding="utf-8")
        print(f"\ntranscript -> {args.transcript}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
