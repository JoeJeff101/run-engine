"""The four orchestration topologies, all against the deterministic stub."""

from __future__ import annotations

import threading

import pytest

from run_engine.agents.backend import CallCapExceeded, OfflineBackend
from run_engine.agents.board import BoardError, load_board
from run_engine.agents.claims import ClaimLedger, run_pool
from run_engine.agents.pipeline import run_pipeline
from run_engine.agents.sequential import run_board
from run_engine.agents.tournament import Option, parse_ledger, red_team, run_tournament

BOARD_PATH = "boards/example_board.yaml"

# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


def test_offline_backend_is_deterministic():
    a, b = OfflineBackend(), OfflineBackend()
    assert a.complete(system="s", prompt="p") == b.complete(system="s", prompt="p")


def test_temperature_changes_the_response():
    backend = OfflineBackend()
    hot = backend.complete(system="s", prompt="p", temperature=1.1)
    cold = backend.complete(system="s", prompt="p", temperature=0.2)
    assert hot != cold


def test_call_cap_is_a_ceiling_not_a_warning():
    backend = OfflineBackend(call_cap=2)
    backend.complete(system="s", prompt="p")
    backend.complete(system="s", prompt="p")
    with pytest.raises(CallCapExceeded):
        backend.complete(system="s", prompt="p")


def test_usage_tracks_tiers_and_estimates_cost():
    backend = OfflineBackend()
    backend.complete(system="s", prompt="p", tier="heavy")
    backend.complete(system="s", prompt="p", tier="light")
    assert backend.usage.by_tier == {"heavy": 1, "light": 1}
    assert backend.usage.estimated_cost() > 0


# ---------------------------------------------------------------------------
# Board validation -- every case here would otherwise fail silently
# ---------------------------------------------------------------------------


def test_example_board_loads_and_has_seventeen_seats():
    board = load_board(BOARD_PATH)
    assert len(board) == 17
    assert board.phases == ["Scoping", "Retrieval", "Stress-Test", "Synthesis"]


def test_every_phase_is_populated():
    """A declared phase with no seats means the board drifted from its docs."""
    grouped = load_board(BOARD_PATH).by_phase()
    assert all(grouped[phase] for phase in grouped), grouped


def _write_board(tmp_path, seats: str, name="b.yaml"):
    path = tmp_path / name
    path.write_text(f"name: T\nsubject: S\nseats:\n{seats}", encoding="utf-8")
    return path


def test_mistyped_context_reference_is_rejected(tmp_path):
    path = _write_board(tmp_path, """
  - key: a
    title: A
    phase: P
    charter: c
  - key: b
    title: B
    phase: P
    charter: c
    context: [typo]
""")
    with pytest.raises(BoardError, match="not a seat"):
        load_board(path)


def test_forward_dependency_is_rejected(tmp_path):
    path = _write_board(tmp_path, """
  - key: a
    title: A
    phase: P
    charter: c
    context: [b]
  - key: b
    title: B
    phase: P
    charter: c
""")
    with pytest.raises(BoardError, match="declared later"):
        load_board(path)


def test_duplicate_seat_keys_are_rejected(tmp_path):
    path = _write_board(tmp_path, """
  - key: a
    title: A
    phase: P
    charter: c
  - key: a
    title: A2
    phase: P
    charter: c
""")
    with pytest.raises(BoardError, match="duplicate"):
        load_board(path)


def test_unknown_tier_is_rejected(tmp_path):
    path = _write_board(tmp_path, """
  - key: a
    title: A
    phase: P
    charter: c
    tier: gigantic
""")
    with pytest.raises(BoardError, match="unknown tier"):
        load_board(path)


def test_missing_required_field_is_rejected(tmp_path):
    path = _write_board(tmp_path, """
  - key: a
    title: A
    phase: P
""")
    with pytest.raises(BoardError, match="missing required"):
        load_board(path)


# ---------------------------------------------------------------------------
# Topology 1: sequential handoff
# ---------------------------------------------------------------------------


def test_board_runs_every_seat(router):
    run = run_board(load_board(BOARD_PATH), OfflineBackend(), router=router)
    assert len(run.results) == 17
    assert all(r.error is None for r in run.results)


def test_upstream_output_actually_reaches_the_downstream_seat(router):
    """The failure this guards against is silent: a seat with an empty context
    still produces confident output, so only an explicit check catches it."""
    seen: list[str] = []

    class Recording(OfflineBackend):
        def complete(self, system, prompt, **kwargs):
            seen.append(prompt)
            return super().complete(system=system, prompt=prompt, **kwargs)

    board = load_board(BOARD_PATH)
    run_board(board, Recording(), router=router)

    downstream = board.seat("mechanism_analyst")
    assert downstream is not None and downstream.context == ["lead_investigator"]
    with_context = [p for p in seen if "Upstream findings" in p]
    assert with_context, "no seat received an upstream context block"
    assert "Lead Investigator" in with_context[0]


def test_model_tiering_does_not_put_every_seat_on_the_heavy_tier(router):
    backend = OfflineBackend()
    run_board(load_board(BOARD_PATH), backend, router=router)
    assert backend.usage.by_tier["light"] > backend.usage.by_tier["heavy"]


def test_board_stops_cleanly_at_the_call_cap_keeping_partial_results(router):
    run = run_board(load_board(BOARD_PATH), OfflineBackend(call_cap=3), router=router)
    assert 0 < len(run.results) < 17
    assert run.results[-1].error and "cap" in run.results[-1].error


def test_retrieval_failure_does_not_halt_the_board():
    class Broken:
        def search(self, **kwargs):
            raise RuntimeError("retrieval exploded")

    run = run_board(load_board(BOARD_PATH), OfflineBackend(), router=Broken())
    assert len(run.results) == 17
    assert all(r.error is None for r in run.results)


def test_board_runs_without_any_router_at_all():
    run = run_board(load_board(BOARD_PATH), OfflineBackend(), router=None)
    assert len(run.results) == 17


def test_seats_are_routed_by_their_profile(router):
    run = run_board(load_board(BOARD_PATH), OfflineBackend(), router=router)
    prior_art = run.by_key()["prior_art_analyst"]
    assert "patentsview" in prior_art.sources_used


# ---------------------------------------------------------------------------
# Topology 2: tournament
# ---------------------------------------------------------------------------


def test_champions_run_at_spread_temperatures():
    result = run_tournament(
        "q",
        [Option("a", "d"), Option("b", "d"), Option("c", "d")],
        OfflineBackend(),
    )
    temps = [p.temperature for p in result.proposals]
    assert len(set(temps)) == len(temps), "identical temperatures defeat the purpose"


def test_judge_emits_exactly_one_incumbent():
    result = run_tournament("q", [Option("a", "d"), Option("b", "d")], OfflineBackend())
    assert sum(1 for v in result.verdicts if v.status == "INCUMBENT") == 1


def test_ledger_parser_drops_malformed_rows():
    verdicts = parse_ledger(
        "<<LEDGER>>\n"
        "good | INCUMBENT | fine\n"
        "bad | NOT-A-STATUS | nope\n"
        "missing-pipe\n"
        "<<END>>"
    )
    assert [v.option for v in verdicts] == ["good"]


def test_standing_ledger_persists_and_is_shown_to_the_next_judge(tmp_path):
    path = tmp_path / "standing.json"
    run_tournament("q", [Option("a", "d")], OfflineBackend(), standing_path=path)
    assert path.is_file()

    seen: list[str] = []

    class Recording(OfflineBackend):
        def complete(self, system, prompt, **kwargs):
            seen.append(prompt)
            return super().complete(system=system, prompt=prompt, **kwargs)

    result = run_tournament("q", [Option("a", "d")], Recording(), standing_path=path)
    assert result.inherited
    assert any("standing ledger" in p for p in seen)


def test_red_team_survival_requires_a_majority():
    class AlwaysRefutes(OfflineBackend):
        def _render(self, prompt, digest, temperature):
            return "<<VERDICT>>\nsupport: UNSUPPORTED\n<<END>>"

    class NeverRefutes(OfflineBackend):
        def _render(self, prompt, digest, temperature):
            return "<<VERDICT>>\nsupport: SUPPORTED\n<<END>>"

    assert red_team("c", AlwaysRefutes(), voters=3)["survives"] is False
    assert red_team("c", NeverRefutes(), voters=3)["survives"] is True


def test_red_team_ties_go_against_the_claim():
    class Alternating(OfflineBackend):
        def __init__(self):
            super().__init__()
            self.n = 0

        def _render(self, prompt, digest, temperature):
            self.n += 1
            verdict = "UNSUPPORTED" if self.n % 2 else "SUPPORTED"
            return f"<<VERDICT>>\nsupport: {verdict}\n<<END>>"

    assert red_team("c", Alternating(), voters=2)["survives"] is False


# ---------------------------------------------------------------------------
# Topology 3: thread-per-role, sole writer
# ---------------------------------------------------------------------------


def test_pipeline_writes_through_the_verifier_only(router):
    writes: list[str] = []
    result = run_pipeline(
        topics=["topic one", "topic two", "topic three"],
        backend=OfflineBackend(),
        router=router,
        workers=2,
        writer=lambda d: writes.append(d.topic),
    )
    assert result.topics_scouted == 3
    assert len(writes) == len(result.written)


def test_only_one_thread_ever_writes(router):
    writer_threads: set[str] = set()

    def record(draft):
        writer_threads.add(threading.current_thread().name)

    run_pipeline(["a", "b", "c", "d"], OfflineBackend(), router=router, workers=3, writer=record)
    assert len(writer_threads) == 1, "writes must come from exactly one thread"
    assert writer_threads == {"verifier"}


def test_untraceable_citation_is_quarantined_not_written(router):
    class Fabricates(OfflineBackend):
        def _render(self, prompt, digest, temperature):
            # A DOI that appears in no retrieved record.
            return "Summary citing 10.9999/invented.reference as support."

    writes: list[str] = []
    result = run_pipeline(
        topics=["a topic"],
        backend=Fabricates(),
        router=router,
        workers=1,
        writer=lambda d: writes.append(d.topic),
    )

    assert writes == []
    assert result.quarantined
    assert "do not trace" in result.quarantined[0][1]


def test_worker_failure_is_recorded_without_killing_the_run(router):
    class Explodes(OfflineBackend):
        def complete(self, system, prompt, **kwargs):
            raise RuntimeError("model unavailable")

    result = run_pipeline(["a", "b"], Explodes(), router=router, workers=2)
    assert result.errors
    assert result.written == []


# ---------------------------------------------------------------------------
# Topology 4: file-locked claim ledger
# ---------------------------------------------------------------------------


def test_a_claim_is_granted_once(tmp_path):
    path = tmp_path / "claims.tsv"
    assert ClaimLedger(path, crew="a").claim("topic-1") is True
    assert ClaimLedger(path, crew="b").claim("topic-1") is False


def test_completed_work_is_never_reclaimed(tmp_path):
    path = tmp_path / "claims.tsv"
    first = ClaimLedger(path, crew="a")
    first.claim("topic-1")
    first.complete("topic-1")
    assert ClaimLedger(path, crew="b", stale_seconds=0).claim("topic-1") is False


def test_a_stale_claim_is_reclaimable(tmp_path):
    """A crew that died must not block a topic forever."""
    path = tmp_path / "claims.tsv"
    ClaimLedger(path, crew="a").claim("topic-1")
    assert ClaimLedger(path, crew="b", stale_seconds=0).claim("topic-1") is True


def test_a_fresh_claim_is_not_reclaimed_under_the_real_window(tmp_path):
    """The other half of staleness: reclaiming too eagerly duplicates work."""
    path = tmp_path / "claims.tsv"
    ClaimLedger(path, crew="a").claim("topic-1")
    assert ClaimLedger(path, crew="b").claim("topic-1") is False


def test_an_unparseable_timestamp_does_not_block_a_topic_forever(tmp_path):
    path = tmp_path / "claims.tsv"
    ClaimLedger(path, crew="a").claim("topic-1")
    corrupted = path.read_text(encoding="utf-8").replace("\tclaimed\t", "\tclaimed\t").splitlines()
    rows = []
    for line in corrupted:
        cells = line.split("\t")
        if cells[0] == "topic-1":
            cells[5] = "not-a-timestamp"
        rows.append("\t".join(cells))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    assert ClaimLedger(path, crew="b").claim("topic-1") is True


def test_two_crews_never_do_the_same_work(tmp_path):
    path = tmp_path / "claims.tsv"
    keys = [f"topic-{i}" for i in range(30)]
    done: list[str] = []
    lock = threading.Lock()

    def work(key: str) -> None:
        with lock:
            done.append(key)

    crew_a = ClaimLedger(path, crew="a")
    crew_b = ClaimLedger(path, crew="b")

    threads = [
        threading.Thread(target=run_pool, args=(keys, crew_a, work, 4)),
        threading.Thread(target=run_pool, args=(keys, crew_b, work, 4)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(done) == sorted(keys)
    assert len(done) == len(set(done)), "a topic was processed twice"


def test_ledger_summary_counts_states(tmp_path):
    path = tmp_path / "claims.tsv"
    ledger = ClaimLedger(path, crew="a")
    ledger.claim("t1")
    ledger.claim("t2")
    ledger.complete("t1")
    assert ledger.summary() == {"total": 2, "claimed": 1, "done": 1}
