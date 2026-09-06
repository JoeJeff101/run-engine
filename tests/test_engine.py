"""The run loop, end to end, against a real pack and the deterministic backend.

These are the tests that would catch the engine quietly ceasing to be governed:
a freeze that stopped being the default, a grounding that started coming from
memory, an archive that could be edited after the fact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from run_engine.engine import (
    ATTACK, DIVERGENCE, FROZEN, THAWED, RunOptions, gate_results, resolve_phase0, run,
)
from run_engine.pack import Pack, PackError
from run_engine.runstate import RunSealed

REPO = Path(__file__).resolve().parents[1]
PACKS = REPO / "packs"


@pytest.fixture(scope="module")
def manufacturing() -> Pack:
    return Pack.load(PACKS / "manufacturing")


def ledger(path: Path, rows: list[tuple[str, str, str, str, str]]) -> Path:
    """Write an authoritative ledger in the format the promotion script emits."""
    head = ("| id | topic | claim | value | grade | source | origin | run | staged_at |\n"
            "|---|---|---|---|---|---|---|---|---|\n")
    body = "".join(
        f"| {rid} | {topic} | {claim} | {value} | {grade} | CIK: 0000320193 | test | r1 | now |\n"
        for rid, topic, claim, value, grade in rows)
    path.write_text("# Evidence ledger\n\n" + head + body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# The whole loop
# ---------------------------------------------------------------------------


def test_a_run_produces_every_required_artifact(manufacturing, tmp_path):
    result = run(manufacturing, RunOptions(runs_dir=tmp_path))
    present = set(result.folder.contents())

    for required in ("00_grounding.md", "95_value_of_information.md",
                     "96_promotion_candidates.md", "97_redteam_verdict.md",
                     "98_lint_report.md", "99_dossier.md", "_continuity.md",
                     "_ledger_snapshot.tsv", "_prediction_log.jsonl", "run.json",
                     "report.html"):
        assert required in present, f"missing {required}"

    task_notes = [n for n in present if n.startswith("T") and n.endswith(".md")]
    assert len(task_notes) == len(manufacturing.tasks)


def test_the_run_is_sealed_and_the_archive_is_immutable(manufacturing, tmp_path):
    result = run(manufacturing, RunOptions(runs_dir=tmp_path))
    assert result.folder.sealed
    with pytest.raises(RunSealed):
        result.folder.write("99_dossier.md", "a more flattering version")


def test_two_offline_runs_produce_identical_documents(manufacturing, tmp_path):
    """Reproducibility is a property this engine sells, so it is tested rather
    than asserted. Timestamps and run ids differ; nothing else may."""
    a = run(manufacturing, RunOptions(runs_dir=tmp_path / "a"))
    b = run(manufacturing, RunOptions(runs_dir=tmp_path / "b"))

    for name in ("99_dossier.md", "98_lint_report.md", "95_value_of_information.md"):
        left = a.folder.read(name).replace(a.run_id, "RUN")
        right = b.folder.read(name).replace(b.run_id, "RUN")
        assert left == right, f"{name} differs between two identical offline runs"


def test_the_engines_own_dossier_passes_the_engines_own_linter(manufacturing, tmp_path):
    """Exempting our own writing from our own rule would be the most obvious
    possible failure of the whole exercise."""
    result = run(manufacturing, RunOptions(runs_dir=tmp_path))
    assert result.lint.blocking == (), \
        f"our dossier trips our own linter: {[f.rule for f in result.lint.blocking]}"


# ---------------------------------------------------------------------------
# Invariant 1: frozen by default
# ---------------------------------------------------------------------------


def test_exploration_is_frozen_until_evidence_thaws_it(manufacturing, tmp_path):
    """Invariant 1. Frozen by default; a newly promoted REAL row is the only
    automatic thaw. Flags are the only other way in, and they are explicit."""
    evidence = tmp_path / "evidence.md"
    ledger(evidence, [])
    runs = tmp_path / "runs"

    first = run(manufacturing, RunOptions(runs_dir=runs, evidence=evidence))
    assert first.phase0 == FROZEN, "a flagless first run must not explore"

    # A run with no new evidence stays frozen, however much anyone would like to
    # reconsider the model.
    second = run(manufacturing, RunOptions(runs_dir=runs, evidence=evidence))
    assert second.phase0 == FROZEN

    # A promoted REAL row earns the right to reconsider.
    ledger(evidence, [("EV-001", "G0", "pre-order test", "40/900", "REAL")])
    third = run(manufacturing, RunOptions(runs_dir=runs, evidence=evidence))
    assert third.phase0 == THAWED

    # And the thaw is spent: the run after it is frozen again.
    fourth = run(manufacturing, RunOptions(runs_dir=runs, evidence=evidence))
    assert fourth.phase0 == FROZEN


def test_an_est_row_does_not_thaw_the_freeze(manufacturing, tmp_path):
    evidence = tmp_path / "evidence.md"
    ledger(evidence, [])
    runs = tmp_path / "runs"
    run(manufacturing, RunOptions(runs_dir=runs, evidence=evidence))

    ledger(evidence, [("EV-001", "G0", "a promising conversation", "pass", "EST")])
    second = run(manufacturing, RunOptions(runs_dir=runs, evidence=evidence))
    assert second.phase0 == FROZEN, "a guess must not buy the right to explore"


def test_attack_and_divergence_are_explicit_and_write_their_own_files(manufacturing, tmp_path):
    """Both branches land in their own numbered file so neither can be quietly
    merged into the optimistic case."""
    attack = run(manufacturing, RunOptions(runs_dir=tmp_path / "atk", attack=True))
    assert attack.phase0 == ATTACK
    assert "00b_attack.md" in attack.folder.contents()

    diverge = run(manufacturing, RunOptions(runs_dir=tmp_path / "div", divergence=True))
    assert diverge.phase0 == DIVERGENCE
    assert "00a_divergence.md" in diverge.folder.contents()

    plain = run(manufacturing, RunOptions(runs_dir=tmp_path / "plain"))
    assert "00a_divergence.md" not in plain.folder.contents()
    assert "00b_attack.md" not in plain.folder.contents()


def test_resolve_phase0_defaults_to_frozen_with_no_history():
    assert resolve_phase0(RunOptions(), real_rows=0, previous=[]) == FROZEN
    assert resolve_phase0(RunOptions(), real_rows=99, previous=[]) == FROZEN, \
        "evidence that predates the engine is not news"


# ---------------------------------------------------------------------------
# Invariant 8: re-grounded from artifacts, never from memory
# ---------------------------------------------------------------------------


def test_every_agent_is_reground_from_artifacts_not_memory(manufacturing, tmp_path):
    """Invariant 8. Grounding is rebuilt every run from the brief, the current
    spec, the persona and the ledger. Nothing carries a session forward."""
    result = run(manufacturing, RunOptions(runs_dir=tmp_path))
    grounding = result.folder.read("00_grounding.md")

    assert manufacturing.brief.one_line() in grounding
    assert f"spec v{manufacturing.spec.version}" in grounding
    assert manufacturing.contract.real_requires.split()[0] in grounding
    assert manufacturing.substrate.key.name in grounding
    for gate in manufacturing.spec.gates:
        assert gate.id in grounding, f"{gate.id} missing from the grounding"


def test_grounding_reflects_a_ratified_spec_change_immediately(manufacturing, tmp_path):
    """Version drift is impossible: agents reason from the current spec, never a
    stale copy."""
    from run_engine.spec import propose, ratify, vote

    before = manufacturing.grounding()
    assert f"v{manufacturing.spec.version}" in before

    amendment = propose(manufacturing.spec, "tighten the margin target",
                        "master_metric.performance", "contribution margin >= 40%", by="cost")
    vote(manufacturing.spec, amendment.id, "demand", "APPROVE")
    ratify(manufacturing.spec, amendment.id, by="qa_lead")

    after = manufacturing.grounding()
    assert "40%" in after
    assert f"v{manufacturing.spec.version}" in after
    assert before != after

    # Leave the module-scoped fixture as we found it.
    manufacturing.spec.version -= 1
    manufacturing.spec.amendments.pop()
    manufacturing.spec.changelog.pop()
    from dataclasses import replace
    manufacturing.spec.master_metric = replace(
        manufacturing.spec.master_metric,
        performance="contribution margin >= 35% at <= 2,000 units per month")


def test_grounding_carries_the_ledger_and_says_so_when_it_is_empty(manufacturing):
    assert "ledger is empty" in manufacturing.grounding().lower()


# ---------------------------------------------------------------------------
# Gates are read off evidence, not off the argument
# ---------------------------------------------------------------------------


def test_a_gate_passes_only_when_a_real_row_says_so(manufacturing, tmp_path):
    from run_engine.engine import load_evidence

    evidence = tmp_path / "evidence.md"
    ledger(evidence, [
        ("EV-001", "G0", "pre-order test", "pass", "REAL"),
        ("EV-002", "G1", "pilot", "pass", "EST"),        # a guess decides nothing
        ("EV-003", "G2", "stress grid", "40/50", "REAL"),  # informs, does not decide
    ])
    results = gate_results(manufacturing, load_evidence(evidence))
    assert results == {"G0": "pass"}


def test_a_failed_gate_blocks_the_run_and_names_where_it_routes(manufacturing, tmp_path):
    evidence = tmp_path / "evidence.md"
    ledger(evidence, [("EV-001", "G0", "pre-order test", "fail", "REAL")])
    result = run(manufacturing, RunOptions(runs_dir=tmp_path / "runs", evidence=evidence))

    assert result.ladder.failed_at == "G0"
    assert result.ladder.routed_to == manufacturing.lock_task
    assert set(result.ladder.defunded) == {"G1", "G2", "G3"}
    assert result.metric.value == 0.0, "a blocked plan does not score"
    assert "do not fund" in result.folder.read("99_dossier.md").lower()


# ---------------------------------------------------------------------------
# The machine-readable record
# ---------------------------------------------------------------------------


def test_run_json_records_what_a_later_run_needs(manufacturing, tmp_path):
    result = run(manufacturing, RunOptions(runs_dir=tmp_path))
    data = json.loads(result.folder.read("run.json"))

    assert data["phase0"] == FROZEN
    assert data["spec_version"] == manufacturing.spec.version
    assert set(data["gates"]) == {g.id for g in manufacturing.spec.gates}
    assert data["probability"]["real_fraction"] == 0.0
    assert data["real_rows"] == 0
    assert isinstance(data["open_items"], list) and data["open_items"]


def test_predictions_are_logged_before_the_gates_are_evaluated(manufacturing, tmp_path):
    result = run(manufacturing, RunOptions(runs_dir=tmp_path))
    lines = [json.loads(line) for line in
             result.folder.read("_prediction_log.jsonl").splitlines() if line.strip()]

    terms = {entry["term"] for entry in lines}
    assert {g.id for g in manufacturing.spec.gates} <= terms
    assert all(entry["outcome"] is None for entry in lines), \
        "a prediction logged with its outcome already attached is not a prediction"


def test_the_html_report_states_all_three_figures(manufacturing, tmp_path):
    result = run(manufacturing, RunOptions(runs_dir=tmp_path))
    page = result.folder.read("report.html")
    assert "REAL fraction" in page
    assert "credible interval" in page
    assert "Gate ladder" in page
    assert "<!doctype html>" in page.lower()


# ---------------------------------------------------------------------------
# Packs must be internally consistent before a run can spend anything
# ---------------------------------------------------------------------------


def test_a_pack_whose_task_names_an_absent_seat_is_refused(tmp_path):
    import shutil
    root = tmp_path / "broken"
    shutil.copytree(PACKS / "manufacturing", root)
    tasks = (root / "tasks.yaml").read_text()
    (root / "tasks.yaml").write_text(tasks.replace("seat: market_analyst",
                                                   "seat: nonexistent_seat"))
    with pytest.raises(PackError, match="not on the board"):
        Pack.load(root)


# ---------------------------------------------------------------------------
# The second, hostile headline
# ---------------------------------------------------------------------------


def test_a_hostile_reading_of_the_ledger_produces_a_second_headline(manufacturing, tmp_path):
    """The red team names rows it cannot accept; the engine demotes exactly
    those and re-runs the same arithmetic. Two numbers from one procedure --
    the model never supplies a probability, only identifiers.
    """
    from run_engine.agents.backend import OfflineBackend

    evidence = tmp_path / "evidence.md"
    ledger(evidence, [("EV-001", "G0", "pre-order test", "pass", "REAL")])

    class Hostile(OfflineBackend):
        def complete(self, system="", prompt="", **kwargs):
            if "hostile reviewer" in system:
                return "EV-001"
            return super().complete(system=system, prompt=prompt, **kwargs)

    result = run(manufacturing, RunOptions(runs_dir=tmp_path / "runs", evidence=evidence),
                 backend=Hostile())

    assert result.conservative.mean < result.estimate.mean, (
        "demoting the row the adversary rejected must lower the headline"
    )
    note = result.folder.read("97_redteam_verdict.md")
    assert "hostile reading" in note.lower()
    assert "EV-001" in note


def test_with_nothing_promoted_the_two_headlines_agree(manufacturing, tmp_path):
    """Both shipped packs have an empty ledger, so the hostile reading is
    trivially equal to the reported one -- and the note says it means nothing."""
    evidence = tmp_path / "evidence.md"
    ledger(evidence, [])

    result = run(manufacturing, RunOptions(runs_dir=tmp_path / "runs", evidence=evidence))

    assert result.conservative.mean == result.estimate.mean
    assert "means nothing yet" in result.folder.read("97_redteam_verdict.md")
