"""End-to-end retrieval demo.

    python -m run_engine.demo "solid-state electrolyte interface stability"
    python -m run_engine.demo --offline "any question"      # no network

Shows the whole path: question -> keywords -> intent -> source chain -> dedup ->
staged evidence. Live mode uses only the keyless sources, so it works with no
credentials at all; key-gated connectors skip themselves and the chain falls
through.
"""

from __future__ import annotations

import argparse

from .evidence.ledger import Row, StagingLedger, grade_for, source_string
from .retrieval.profiles import INTENTS, resolve_intents
from .retrieval.query import keywordize
from .retrieval.router import Router


def _print_header(question: str, intents: list[str], budget: str, offline: bool) -> None:
    print(f"question : {question}")
    print(f"keywords : {keywordize(question)}")
    print(f"intent   : {intents[0]}")
    print(f"chain    : {' -> '.join(INTENTS[intents[0]]['chain'])}")
    print(f"budget   : {budget}")
    print(f"mode     : {'offline (fake sources)' if offline else 'live (keyless sources)'}")
    print(f"rationale: {INTENTS[intents[0]]['note']}")
    print()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="*", help="the research question")
    ap.add_argument("--intent", choices=sorted(INTENTS), help="force an intent")
    ap.add_argument("--scope", help="coarse dial: all, literature, patents, entities, biomed")
    ap.add_argument("--seat", help="resolve the intent from a board seat profile")
    ap.add_argument("--budget", default="standard", choices=["lean", "standard", "deep"])
    ap.add_argument("--offline", action="store_true", help="use deterministic fake sources")
    ap.add_argument("--cache", metavar="DIR", help="enable the on-disk query cache")
    ap.add_argument("--stage", metavar="PATH", help="stage top results to an evidence file")
    ap.add_argument("--apply", action="store_true", help="actually write staged rows")
    args = ap.parse_args(argv)

    question = " ".join(args.question).strip() or "solid-state electrolyte interface stability"
    intents = resolve_intents(intent=args.intent, scope=args.scope, seat=args.seat)
    _print_header(question, intents, args.budget, args.offline)

    if args.offline:
        from .testing import fake_adapters
        router = Router(adapters=fake_adapters(), cache_dir=args.cache)
    else:
        router = Router(cache_dir=args.cache)

    result = router.search(
        question=question,
        intent=args.intent,
        scope=args.scope,
        seat=args.seat,
        budget=args.budget,
    )

    if not result.records:
        print("no records returned.")
        print(f"  sources queried : {', '.join(result.sources_queried) or 'none'}")
        print(f"  breaker tripped : {', '.join(result.breaker_tripped) or 'none'}")
        print("\nNote: an empty result means the sources answered and had nothing,")
        print("or were unavailable. Those are different things -- check the breaker.")
        return 0

    print(f"{len(result.records)} record(s) after dedup:\n")
    for i, rec in enumerate(result.records, start=1):
        ident = rec.doi or rec.entity_id or rec.pmid or "no identifier"
        print(f"  [{i:>2}] {rec.title[:78]}")
        print(f"       {rec.source} | {rec.year or 'n.d.'} | {rec.authority} | {rec.role} | {ident}")
        snippet = rec.best_text(140)
        if snippet:
            print(f"       {snippet}")
    print()

    print(f"sources queried : {', '.join(result.sources_queried)}")
    print(f"fallbacks used  : {', '.join(result.fallbacks_used) or 'none'}")
    print(f"breaker tripped : {', '.join(result.breaker_tripped) or 'none'}")
    print(f"retrieval       : {router.savings_report()}")

    if args.stage:
        ledger = StagingLedger(args.stage, run_id="demo")
        rows = [
            Row(
                topic=question[:120],
                claim=f"source consulted: {rec.source}",
                value=rec.title[:160],
                grade=grade_for(rec),
                source=source_string(rec),
                origin="demo",
            )
            for rec in result.records[:4]
        ]
        staged = ledger.stage(rows, dry=not args.apply)
        verb = "staged" if args.apply else "would stage"
        print(f"\n{verb} {len(staged)} row(s) -> {args.stage}")
        for row in staged:
            print(f"  [{row.grade}] {row.value[:60]}  <- {row.source[:60]}")
        if not args.apply:
            print("  (dry run: pass --apply to write)")
        print("\nStaging is not promotion. Nothing here counts as evidence until a")
        print("human promotes it:  python -m run_engine.evidence.ledger <staged> <authoritative>")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
