"""Agent orchestration.

Four topologies, each suited to a different shape of problem:

``sequential``  handoff with declared context -- when order matters and each
                step builds on the last.
``tournament``  temperature-diverged fan-out plus a judge -- when several
                approaches compete and you need one chosen defensibly.
``pipeline``    thread-per-role with a sole writer -- when throughput matters
                and output correctness must not be routable-around.
``claims``      file-locked worker pool -- when several processes share a work
                list and must provably not duplicate each other.

All four run end to end against ``backend.OfflineBackend`` with no credentials.
"""

from __future__ import annotations

from .backend import (
    CallCapExceeded,
    LLMBackend,
    OfflineBackend,
    Usage,
    default_backend,
    model_for,
)
from .board import Board, BoardError, Seat, load_board
from .claims import Claim, ClaimLedger, run_pool
from .pipeline import Draft, PipelineResult, run_pipeline
from .sequential import BoardRun, SeatResult, run_board
from .tournament import Option, TournamentResult, Verdict, red_team, run_tournament

__all__ = [
    "LLMBackend", "OfflineBackend", "Usage", "CallCapExceeded", "default_backend", "model_for",
    "Board", "Seat", "BoardError", "load_board",
    "run_board", "BoardRun", "SeatResult",
    "run_tournament", "red_team", "Option", "Verdict", "TournamentResult",
    "run_pipeline", "Draft", "PipelineResult",
    "ClaimLedger", "Claim", "run_pool",
]
