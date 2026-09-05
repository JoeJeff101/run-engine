"""Run folders, continuity, the plateau flag, and the deterministic linter."""

from __future__ import annotations

import json

import pytest

from run_engine.lint import ADVISORY, BLOCKING, lint_dossier
from run_engine.runstate import (
    Continuity, OpenItem, RunFolder, RunSealed, detect_plateau, history_for_plateau,
    latest_continuity, load_runs,
)


# ---------------------------------------------------------------------------
# Run folders are immutable once sealed
# ---------------------------------------------------------------------------


def test_a_sealed_run_folder_refuses_further_writes(tmp_path):
    """An archive that can be revised after the fact is a draft, and a draft
    cannot be evidence of what was believed at the time."""
    folder = RunFolder.create(tmp_path)
    folder.write("99_dossier.md", "# Dossier\n")
    folder.seal()

    assert folder.sealed
    with pytest.raises(RunSealed, match="sealed"):
        folder.write("99_dossier.md", "# Dossier, revised\n")
    with pytest.raises(RunSealed):
        folder.write_json("run.json", {"tampered": True})
    with pytest.raises(RunSealed):
        folder.append_jsonl("_prediction_log.jsonl", {"predicted": 0.99})

    assert folder.read("99_dossier.md") == "# Dossier\n"


def test_run_folders_do_not_collide_within_the_same_second(tmp_path):
    a = RunFolder.create(tmp_path)
    b = RunFolder.create(tmp_path)
    assert a.root != b.root


def test_a_run_folder_lists_its_contents_without_the_seal_marker(tmp_path):
    folder = RunFolder.create(tmp_path)
    folder.write("99_dossier.md", "x")
    folder.write("run.json", "{}")
    folder.seal()
    assert folder.contents() == ["99_dossier.md", "run.json"]


def test_failed_runs_are_archived_too(tmp_path):
    """The failures are the diligence trail."""
    folder = RunFolder.create(tmp_path)
    folder.write_json("run.json", {"outcome": "blocked", "failed_at": "G2"})
    folder.seal()
    runs = load_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["failed_at"] == "G2"


def test_a_crashed_run_does_not_stop_the_next_one_from_loading(tmp_path):
    good = RunFolder.create(tmp_path, run_id="20260101T000000Z")
    good.write_json("run.json", {"probability": {"mean": 0.2}})
    broken = RunFolder.create(tmp_path, run_id="20260102T000000Z")
    broken.write("run.json", "{not json")
    (tmp_path / "20260103T000000Z").mkdir()  # a folder with no run.json at all

    runs = load_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["run_id"] == "20260101T000000Z"


# ---------------------------------------------------------------------------
# Continuity and the plateau flag
# ---------------------------------------------------------------------------


def test_three_flat_runs_with_no_shrinking_open_items_is_a_plateau():
    assert detect_plateau([(0.170, 5), (0.168, 5), (0.171, 4)])


def test_a_moving_headline_is_not_a_plateau():
    assert not detect_plateau([(0.170, 5), (0.310, 5), (0.171, 5)])


def test_shrinking_open_items_is_progress_even_at_a_flat_headline():
    """A headline that holds still while questions get answered is not spinning."""
    assert not detect_plateau([(0.170, 2), (0.168, 4), (0.171, 6)])


def test_too_little_history_is_not_a_plateau():
    assert not detect_plateau([(0.17, 5), (0.17, 5)])


def test_an_unscored_run_cannot_establish_a_plateau():
    assert not detect_plateau([(None, 5), (0.17, 5), (0.17, 5)])


def test_the_plateau_note_says_the_constraint_is_not_analysis():
    continuity = Continuity(
        open_items=[OpenItem("OI-1", "is the category structurally commodity?")],
        headline=0.17, plateau=True)
    text = continuity.to_markdown("20260101T000000Z")
    assert "PLATEAU" in text
    assert "money or access, not analysis" in text
    assert "OI-1" in text


def test_continuity_carries_unresolved_items_forward(tmp_path):
    folder = RunFolder.create(tmp_path)
    folder.write_json("run.json", {
        "probability": {"mean": 0.31},
        "open_items": [
            {"id": "OI-1", "question": "freight exposure", "status": "open"},
            {"id": "OI-2", "question": "settled", "status": "closed"},
        ],
    })
    carried = latest_continuity(tmp_path)
    assert carried.headline == 0.31
    assert [i.id for i in carried.unresolved()] == ["OI-1"]


def test_history_reduces_runs_to_the_two_series_the_test_needs():
    runs = [{"probability": {"mean": 0.2}, "open_items": [{"id": "a"}, {"id": "b"}]},
            {"probability": {"mean": 0.19}, "open_items": []},
            {"open_items": []}]
    assert history_for_plateau(runs) == [(0.2, 2), (0.19, 0), (None, 0)]


# ---------------------------------------------------------------------------
# The linter
# ---------------------------------------------------------------------------


def test_lint_flags_a_figure_with_nothing_behind_it():
    report = lint_dossier("Landed COGS is $42.50 per unit.")
    assert not report.ok
    assert report.blocking[0].rule == "unsourced-figure"


def test_lint_accepts_a_figure_that_carries_a_source_or_a_tag():
    assert lint_dossier("Landed COGS is $42.50 per unit [EST].").ok
    assert lint_dossier("Landed COGS is $42.50 per unit (source: factory quote).").ok


def test_lint_refuses_a_real_claim_with_no_document():
    report = lint_dossier("| unit cost | 42.50 | REAL | our best understanding |")
    rules = {f.rule for f in report.blocking}
    assert "real-without-document" in rules
    assert "earned by a document" in report.to_markdown() or any(
        "document" in f.message for f in report.blocking)


def test_lint_accepts_a_real_claim_backed_by_an_identifier():
    text = "| gross margin | 38% | REAL | CIK: 0000320193 |"
    assert lint_dossier(text).ok


def test_lint_catches_a_total_that_does_not_reconcile():
    text = "\n".join([
        "| Component | Cost |",
        "|---|---:|",
        "| Housing | 10.00 |",
        "| Filter | 5.00 |",
        "| Total | 25.00 |",
    ])
    report = lint_dossier(text)
    assert any(f.rule == "unreconciled-total" for f in report.blocking)


def test_lint_accepts_a_total_that_reconciles():
    text = "\n".join([
        "| Component | Cost |",
        "|---|---:|",
        "| Housing | 10.00 |",
        "| Filter | 5.00 |",
        "| Total | 15.00 |",
    ])
    assert not any(f.rule == "unreconciled-total" for f in lint_dossier(text).findings)


def test_lint_requires_every_declared_gate_to_record_a_result():
    text = "G0 — PASS — anyone pre-ordering?\nG1 was discussed at length.\n"
    report = lint_dossier(text, gate_ids=["G0", "G1", "G2"])
    messages = " ".join(f.message for f in report.blocking)
    assert "G1 appears but records no result" in messages
    assert "G2 is declared in the spec but never appears" in messages
    assert "G0" not in messages


def test_lint_flags_authority_attributed_to_nobody():
    report = lint_dossier("Studies show the category is growing.")
    assert any(f.rule == "unattributed-authority" and f.severity == ADVISORY
               for f in report.findings)
    assert report.ok, "sloppy prose is advisory, not blocking"


def test_lint_catches_a_narrative_that_disagrees_with_the_arithmetic():
    report = lint_dossier("We assess P(success) at 62%.", expected_headline=0.17)
    assert any(f.rule == "headline-mismatch" for f in report.blocking)
    assert "disagree" in " ".join(f.message for f in report.blocking)


def test_lint_requires_a_headline_when_one_is_expected():
    report = lint_dossier("No number here at all.", expected_headline=0.17)
    assert any(f.rule == "headline-missing" for f in report.blocking)


def test_lint_ignores_fenced_code_and_headings():
    text = "\n".join([
        "# Costs of $1,000,000",
        "```",
        "unit_cost = 42.50",
        "```",
        "Everything above is illustration.",
    ])
    assert lint_dossier(text).ok, "examples and headings are not claims"


def test_the_lint_report_is_readable_when_clean():
    text = lint_dossier("Nothing to see.").to_markdown()
    assert "No findings" in text


def test_the_linter_imports_nothing_that_can_think():
    """G15b, asserted from inside the suite as well as from the shell."""
    import pathlib
    source = pathlib.Path(
        __file__).resolve().parents[1] / "src" / "run_engine" / "lint.py"
    body = source.read_text(encoding="utf-8")
    for banned in ("backend", "anthropic", "openai", "LLMBackend"):
        assert f"import {banned}" not in body
        assert f"from {banned}" not in body


def test_lint_does_not_mistake_an_identifier_for_a_quantity():
    """A digit run inside an opaque identifier is not a claim anyone can source."""
    assert lint_dossier("- **Pharmacologist** — [offline:46275215] Findings follow.").ok
    assert lint_dossier("Run 20260905T143513Z completed.").ok
    # But a genuine figure on the same line is still caught.
    assert not lint_dossier("[offline:46275215] Landed cost is $42.50.").ok
