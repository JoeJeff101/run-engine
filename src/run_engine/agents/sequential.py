"""Topology 1: sequential handoff with explicit context chaining.

Each seat runs in declared order and receives, verbatim, the output of the
upstream seats it named. Nothing is implicit -- a seat sees exactly what its
``context`` list says it sees, and the board loader refuses to start if any of
those references is wrong.

Why not just let every agent see everything? Two reasons, and both showed up in
practice. Shared-everything context grows quadratically with the roster, so a
seventeen-seat board becomes unaffordable somewhere around seat nine. And it
destroys the value of having distinct seats at all: once every agent has read
every other agent's reasoning, they converge, and seventeen correlated opinions
are worth roughly one opinion.

Declared narrow context keeps seats genuinely independent where independence is
the point, and connects them precisely where the handoff actually matters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..evidence.ledger import Row, StagingLedger, row_from_record
from ..evidence.models import ResearchResult
from .backend import CallCapExceeded, LLMBackend, resolve_tier
from .board import Board, Seat

# The retrieval context slot. Bounded per seat so one enthusiastic seat cannot
# consume the run's whole token budget.
RETRIEVAL_CHARS = 6_000


@dataclass
class SeatResult:
    seat: str
    title: str
    phase: str
    output: str
    tier: str
    sources_used: list[str] = field(default_factory=list)
    records_seen: int = 0
    error: str | None = None


@dataclass
class BoardRun:
    board: str
    subject: str
    results: list[SeatResult] = field(default_factory=list)
    staged: list[Row] = field(default_factory=list)
    usage_report: str = ""

    def by_key(self) -> dict[str, SeatResult]:
        return {r.seat: r for r in self.results}

    def transcript(self) -> str:
        lines = [f"# {self.board}", f"_{self.subject}_", ""]
        phase = None
        for result in self.results:
            if result.phase != phase:
                phase = result.phase
                lines.append(f"\n## {phase}\n")
            status = f" [ERROR: {result.error}]" if result.error else ""
            srcs = f"  (sources: {', '.join(result.sources_used)})" if result.sources_used else ""
            lines.append(f"### {result.title} [{result.tier}]{status}{srcs}")
            lines.append(result.output.strip() or "(no output)")
            lines.append("")
        return "\n".join(lines)


def _context_block(seat: Seat, done: dict[str, SeatResult]) -> str:
    if not seat.context:
        return ""
    chunks = []
    for key in seat.context:
        upstream = done.get(key)
        if upstream:
            chunks.append(f"--- From {upstream.title} ---\n{upstream.output.strip()}")
    if not chunks:
        return ""
    return "Upstream findings you must build on:\n\n" + "\n\n".join(chunks) + "\n\n"


def _challenge_block(seat: Seat, done: dict[str, SeatResult]) -> str:
    """Material this seat is chartered to attack, not to build on.

    Framing matters more than content here. The same text presented as
    "here is a colleague's finding" produces agreement; presented as "your job
    is to find what is wrong with this" produces an actual critique. The board
    pairs divergent seats with skeptical ones precisely to exploit that.
    """
    if not seat.challenges:
        return ""
    chunks = []
    for key in seat.challenges:
        target = done.get(key)
        if target:
            chunks.append(f"--- Claim under challenge, from {target.title} ---\n{target.output.strip()}")
    if not chunks:
        return ""
    return (
        "You are chartered to ATTACK the following. Do not summarize it and do "
        "not find it broadly reasonable. Identify the weakest load-bearing "
        "assumption, state what evidence would falsify it, and say plainly "
        "whether the retrieved sources support it or merely fail to contradict "
        "it -- those are different things.\n\n" + "\n\n".join(chunks) + "\n\n"
    )


def run_board(
    board: Board,
    backend: LLMBackend,
    router: Any | None = None,
    budget: str = "lean",
    staging: StagingLedger | None = None,
    max_tokens: int = 2048,
    on_seat: Callable[[SeatResult], None] | None = None,
    diverse: bool = False,
) -> BoardRun:
    """Execute a board seat by seat, chaining context forward.

    ``diverse`` routes every seat chartered to attack another onto the outside
    tier, so an attack is not checked by the same training distribution that
    produced what it is attacking.
    """
    run = BoardRun(board=board.name, subject=board.subject)
    done: dict[str, SeatResult] = {}

    for seat in board.seats:
        # A seat that challenges another is the one worth making independent.
        tier = resolve_tier(seat.tier, diverse=diverse, attacks=bool(seat.challenges))
        retrieval_text = ""
        sources_used: list[str] = []
        records_seen = 0
        result: ResearchResult | None = None

        if router is not None and seat.retrieval:
            try:
                result = router.search(
                    question=f"{board.subject} {seat.charter}",
                    seat=seat.key,
                    budget=budget,
                )
                sources_used = list(result.sources_queried)
                records_seen = len(result.records)
                if records_seen:
                    packed = result.pack(pack_chars=RETRIEVAL_CHARS, excerpt_chars=400)
                    retrieval_text = f"Retrieved sources:\n\n{packed}\n\n"
            except Exception as exc:
                # Retrieval is an input, not a precondition. A seat with no
                # sources should say so, not halt the board.
                retrieval_text = f"Retrieval unavailable for this seat ({exc}).\n\n"

        prompt = (
            f"{_context_block(seat, done)}"
            f"{_challenge_block(seat, done)}"
            f"{retrieval_text}"
            f"Produce your contribution as {seat.title}. Be specific about what the "
            f"evidence supports and explicit about what it does not. State your "
            f"coverage: what you searched, what returned nothing, and what you "
            f"could not reach."
        )

        try:
            output = backend.complete(
                system=seat.system_prompt(board.subject, board.rules),
                prompt=prompt,
                tier=tier,
                temperature=seat.temperature,
                max_tokens=max_tokens,
            )
            error = None
        except CallCapExceeded as exc:
            # Hitting the cap ends the board cleanly with partial results,
            # rather than throwing away everything already produced.
            run.results.append(
                SeatResult(seat.key, seat.title, seat.phase, "", tier, error=str(exc))
            )
            break
        except Exception as exc:
            output, error = "", str(exc)

        seat_result = SeatResult(
            seat=seat.key,
            title=seat.title,
            phase=seat.phase,
            output=output,
            tier=tier,
            sources_used=sources_used,
            records_seen=records_seen,
            error=error,
        )
        done[seat.key] = seat_result
        run.results.append(seat_result)
        if on_seat:
            on_seat(seat_result)

        # Stage evidence -- never write it as authoritative. See ledger.py.
        if staging is not None and result is not None and result.records:
            rows = [
                row_from_record(
                    record=rec,
                    topic=board.subject[:120],
                    claim=f"{seat.title}: source consulted",
                    value=rec.title[:160],
                    origin=f"board:{seat.key}",
                )
                for rec in result.records[:4]
            ]
            run.staged.extend(staging.stage(rows, dry=False))

    usage = getattr(backend, "usage", None)
    run.usage_report = usage.report() if usage else ""
    return run
