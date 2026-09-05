"""The mechanisms that make the board more than a prompt chain.

Judge scoring, adversarial pairing, dead-end reformulation, and the identifier
namespaces that let the same discipline apply outside science.
"""

from __future__ import annotations

import pytest

from run_engine.agents.backend import OfflineBackend
from run_engine.agents.board import BoardError, load_board
from run_engine.agents.sequential import run_board
from run_engine.agents.tournament import (
    CRITERIA,
    DEAD_END_FLOOR,
    MAX_SCORE,
    Option,
    PENALTIES,
    Score,
    classify,
    parse_scores,
    run_tournament,
)
from run_engine.evidence.ledger import (
    IDENTIFIER_NAMESPACES,
    PRIMARY_SOURCE_RE,
    REGISTRY_RE,
    grade_for,
    namespaces_in,
)
from run_engine.evidence.models import Record
from run_engine.retrieval.query import broaden, decompose, pivot_to_rare, reformulations
from run_engine.retrieval.router import Router
from run_engine.testing import fake_adapters, fake_source

BOARD_PATH = "boards/example_board.yaml"


# ---------------------------------------------------------------------------
# Identifier namespaces -- the seam that generalizes past science
# ---------------------------------------------------------------------------


def _rec(**kw) -> Record:
    kw.setdefault("title", "t")
    kw.setdefault("source", "s")
    return Record(**kw)


@pytest.mark.parametrize(
    "entity_id",
    [
        "PUBCHEM:2244",          # scientific
        "USPTO:10000000",        # intellectual property
        "CIK: 0000320193",       # corporate
        "0000320193-24-000123",  # corporate filing
        "17 CFR 240.10b-5",      # regulatory
        "No. 1:21-cv-01234",     # legal
    ],
)
def test_registry_identifiers_grade_real_across_domains(entity_id):
    assert grade_for(_rec(entity_id=entity_id)) == "REAL"
    assert REGISTRY_RE.search(entity_id)


@pytest.mark.parametrize(
    "record",
    [
        _rec(doi="10.1126/science.1236098"),
        _rec(pmid="23887888"),
        _rec(entity_id="https://openalex.org/W2119459576"),
    ],
)
def test_citation_identifiers_grade_est_not_real(record):
    """A DOI identifies a document. The document's claim is still a claim."""
    assert grade_for(record) == "EST"


def test_every_namespace_example_matches_its_own_pattern():
    """Guards the table against a pattern that drifts from its documentation."""
    for ns in IDENTIFIER_NAMESPACES:
        assert PRIMARY_SOURCE_RE.search(ns.example), f"{ns.name} example does not match"


def test_namespaces_in_reports_what_it_found():
    found = namespaces_in("see 10.1126/science.1236098 and CIK: 0000320193")
    assert "DOI" in found and "SEC CIK" in found


def test_prose_that_merely_sounds_authoritative_is_not_an_identifier():
    for text in ("industry sources confirm", "per the filing", "well established"):
        assert not PRIMARY_SOURCE_RE.search(text)


# ---------------------------------------------------------------------------
# Judge scoring
# ---------------------------------------------------------------------------


def _score(option, ed=3, sp=3, fa=3, ch=3, penalties=None) -> Score:
    return Score(
        option=option,
        marks={"evidence_density": ed, "specificity": sp,
               "falsifiability": fa, "coverage_honesty": ch},
        penalties=penalties or [],
    )


def test_scorecard_parses():
    scores = parse_scores(
        "<<SCORES>>\n"
        "alpha | evidence_density=5 specificity=4 falsifiability=3 coverage_honesty=2 |  | solid\n"
        "beta  | evidence_density=1 specificity=5 falsifiability=1 coverage_honesty=0 | logical_leap | thin\n"
        "<<END>>"
    )
    assert [s.option for s in scores] == ["alpha", "beta"]
    assert scores[1].penalties == ["logical_leap"]


def test_malformed_rows_are_dropped_not_guessed():
    scores = parse_scores(
        "<<SCORES>>\n"
        "good | evidence_density=4 specificity=3 falsifiability=3 coverage_honesty=3 | | ok\n"
        "no-marks-at-all | | | nothing\n"
        "missing pipes entirely\n"
        "<<END>>"
    )
    assert [s.option for s in scores] == ["good"]


def test_unknown_criteria_and_penalties_are_ignored():
    scores = parse_scores(
        "<<SCORES>>\n"
        "a | evidence_density=4 vibes=5 | not_a_penalty,logical_leap | x\n"
        "<<END>>"
    )
    assert scores[0].marks == {"evidence_density": 4}
    assert scores[0].penalties == ["logical_leap"]


def test_marks_are_clamped_to_the_scale():
    """A judge that writes 99 does not get to outvote the rubric."""
    assert _score("a", ed=99).weighted() == _score("a", ed=5).weighted()


def test_highest_total_becomes_incumbent():
    verdicts = classify([_score("weak", ed=1, sp=1, fa=1, ch=1), _score("strong", ed=5, sp=5, fa=5, ch=5)])
    assert verdicts[0].option == "strong"
    assert verdicts[0].status == "INCUMBENT"
    assert sum(1 for v in verdicts if v.status == "INCUMBENT") == 1


def test_evidence_density_breaks_ties():
    """On an exact tie, the option whose claims are citable wins.

    Weights are evidence_density=3, specificity=2, falsifiability=2,
    coverage_honesty=1, so 4x3 and (5x2 + 2x1) both total 12.
    """
    citable = Score("citable", {"evidence_density": 4})
    eloquent = Score("eloquent", {"specificity": 5, "coverage_honesty": 2})
    assert citable.total() == eloquent.total() == 12

    # Order the input against the expected result so a stable sort cannot pass
    # this test by accident.
    assert classify([eloquent, citable])[0].option == "citable"


def test_a_confident_uncited_argument_loses_to_a_plodding_cited_one():
    """The failure mode the rubric exists to prevent."""
    confident = _score("confident", ed=0, sp=5, fa=4, ch=0,
                       penalties=["confidence_substitution", "uncited_specific"])
    plodding = _score("plodding", ed=5, sp=2, fa=2, ch=4)
    verdicts = {v.option: v for v in classify([confident, plodding])}
    assert verdicts["plodding"].status == "INCUMBENT"
    assert verdicts["confident"].status != "INCUMBENT"


def test_penalties_subtract():
    clean = _score("clean", ed=3, sp=3, fa=3, ch=3)
    penalised = _score("penalised", ed=3, sp=3, fa=3, ch=3, penalties=["uncited_specific"])
    assert penalised.total() == clean.total() - 4


def test_a_uniformly_weak_field_yields_no_incumbent():
    """Better to report no winner than to launder a weak field into a decision."""
    verdicts = classify([_score("a", 0, 0, 0, 0), _score("b", 1, 0, 0, 0)])
    assert all(v.status == "DEAD-END" for v in verdicts)
    assert not any(v.status == "INCUMBENT" for v in verdicts)


def test_dead_end_floor_is_a_fraction_of_the_maximum():
    assert 0 < DEAD_END_FLOOR < MAX_SCORE
    assert MAX_SCORE == sum(c.weight * 5 for c in CRITERIA)


def test_evidence_density_is_the_heaviest_criterion():
    weights = {c.key: c.weight for c in CRITERIA}
    assert weights["evidence_density"] == max(weights.values())
    assert weights["evidence_density"] > weights["specificity"]


def test_every_penalty_is_negative():
    assert all(p.points < 0 for p in PENALTIES)


def test_tournament_produces_a_parsed_scorecard_offline():
    result = run_tournament("q", [Option("a", "d"), Option("b", "d")], OfflineBackend())
    assert result.scorecard_parsed
    assert result.scores
    assert result.verdicts


def test_unreadable_scorecard_is_recorded_as_a_signal():
    class Rambles(OfflineBackend):
        def _render(self, prompt, digest, temperature):
            if "RESPOND_WITH: scores" in prompt:
                return "I think option a is probably the best one overall."
            return super()._render(prompt, digest, temperature)

    result = run_tournament("q", [Option("a", "d")], Rambles())
    assert result.scorecard_parsed is False


def test_the_judge_is_shown_the_rubric():
    seen: list[str] = []

    class Recording(OfflineBackend):
        def complete(self, system, prompt, **kw):
            seen.append(prompt)
            return super().complete(system=system, prompt=prompt, **kw)

    run_tournament("q", [Option("a", "d")], Recording())
    judge_prompt = seen[-1]
    for c in CRITERIA:
        assert c.key in judge_prompt


def test_standing_ledger_persists_status_not_marks(tmp_path):
    """A later judge inherits what was decided, not a previous judge's numbers."""
    path = tmp_path / "standing.json"
    run_tournament("q", [Option("a", "d")], OfflineBackend(), standing_path=path)
    body = path.read_text(encoding="utf-8")
    assert "status" in body
    assert "evidence_density" not in body


# ---------------------------------------------------------------------------
# Adversarial pairing
# ---------------------------------------------------------------------------


def test_challenged_seat_receives_an_attack_framing(router):
    seen: list[str] = []

    class Recording(OfflineBackend):
        def complete(self, system, prompt, **kw):
            seen.append(prompt)
            return super().complete(system=system, prompt=prompt, **kw)

    board = load_board(BOARD_PATH)
    assert any(s.challenges for s in board.seats), "example board defines no pairing"

    run_board(board, Recording(), router=router)
    attacks = [p for p in seen if "chartered to ATTACK" in p]
    assert attacks, "no seat was given adversarial framing"
    assert "Claim under challenge" in attacks[0]


def test_challenging_an_unknown_seat_is_rejected(tmp_path):
    path = tmp_path / "b.yaml"
    path.write_text(
        "name: T\nsubject: S\nseats:\n"
        "  - key: a\n    title: A\n    phase: P\n    charter: c\n"
        "  - key: b\n    title: B\n    phase: P\n    charter: c\n    challenges: [ghost]\n",
        encoding="utf-8",
    )
    with pytest.raises(BoardError, match="chartered to challenge"):
        load_board(path)


def test_challenging_a_later_seat_is_rejected(tmp_path):
    path = tmp_path / "b.yaml"
    path.write_text(
        "name: T\nsubject: S\nseats:\n"
        "  - key: a\n    title: A\n    phase: P\n    charter: c\n    challenges: [b]\n"
        "  - key: b\n    title: B\n    phase: P\n    charter: c\n",
        encoding="utf-8",
    )
    with pytest.raises(BoardError, match="has not been made yet"):
        load_board(path)


def test_charter_rules_carry_the_effort_and_disagreement_directives():
    board = load_board(BOARD_PATH)
    rules = board.rules.lower()
    for directive in ("abstain", "coverage", "alternative", "disagree", "refute"):
        assert directive in rules, f"charter is missing the {directive!r} directive"


# ---------------------------------------------------------------------------
# Dead-end reformulation
# ---------------------------------------------------------------------------


def test_broaden_keeps_the_longest_terms_in_original_order():
    assert broaden("thermal stability solid state electrolyte interfaces", keep=3) == (
        "stability electrolyte interfaces"
    )


def test_decompose_produces_overlapping_halves():
    halves = decompose("a bb ccc dddd eeeee")
    assert len(halves) == 2
    assert set(halves[0].split()) & set(halves[1].split()), "halves must share a pivot term"


def test_reformulations_never_repeat_the_failed_query():
    query = "thermal stability solid state electrolyte"
    assert all(q != query for _, q in reformulations(query))


def test_reformulations_are_deduplicated():
    pairs = reformulations("alpha beta gamma delta epsilon")
    assert len({q for _, q in pairs}) == len(pairs)


def test_a_dead_end_triggers_reformulation(calls):
    """Empty is usually a badly-shaped query, not an empty field."""
    adapters = {k: fake_source(k, empty=True, calls=calls) for k in fake_adapters()}
    router = Router(adapters=adapters, sleep=lambda _: None)

    result = router.search("thermal stability of solid state electrolyte interfaces",
                           intent="discovery", budget="lean")

    assert result.records == []
    assert result.reformulations, "a dead end must be retried differently"
    assert [r["strategy"] for r in result.reformulations][0] == "broaden"


def test_reformulation_stops_as_soon_as_something_returns():
    attempts: list[str] = []

    def picky(query, **kwargs):
        attempts.append(query)
        if len(attempts) == 1:
            return []
        return list(fake_source("openalex", count=2)(query=query))

    router = Router(adapters={"openalex": picky}, sleep=lambda _: None)
    result = router.search("thermal stability solid state electrolyte", intent="discovery")

    assert result.records
    assert len(result.reformulations) == 1


def test_no_reformulation_when_the_first_attempt_succeeds(router):
    result = router.search("a question", intent="discovery", budget="lean")
    assert result.records
    assert result.reformulations == []


def test_reformulation_can_be_disabled(calls):
    adapters = {k: fake_source(k, empty=True, calls=calls) for k in fake_adapters()}
    router = Router(adapters=adapters, sleep=lambda _: None)
    result = router.search("a b c d e", intent="discovery", reformulate=False)
    assert result.reformulations == []


def test_reformulation_is_bounded(calls):
    adapters = {k: fake_source(k, empty=True, calls=calls) for k in fake_adapters()}
    router = Router(adapters=adapters, sleep=lambda _: None)
    result = router.search("alpha beta gamma delta epsilon zeta",
                           intent="discovery", max_reformulations=2)
    assert len(result.reformulations) <= 2


def test_an_agent_supplied_reformulator_is_used(calls):
    adapters = {k: fake_source(k, empty=True, calls=calls) for k in fake_adapters()}
    router = Router(adapters=adapters, sleep=lambda _: None)

    result = router.search(
        "alpha beta gamma delta",
        intent="discovery",
        reformulator=lambda q: ["a completely different framing"],
        max_reformulations=9,
    )
    assert any(r["strategy"] == "agent" for r in result.reformulations)


def test_a_failing_reformulator_does_not_fail_the_search(calls):
    adapters = {k: fake_source(k, empty=True, calls=calls) for k in fake_adapters()}
    router = Router(adapters=adapters, sleep=lambda _: None)

    def broken(_q):
        raise RuntimeError("the reformulator exploded")

    result = router.search("alpha beta gamma delta", intent="discovery", reformulator=broken)
    assert result.records == []
    assert result.reformulations, "structural reformulations still ran"
