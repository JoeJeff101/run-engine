"""Gates, probability, value of information, calibration.

The arithmetic half of the engine. These tests exist because a probability is
the easiest thing in a system like this to fake: it looks precise, it is hard
to check by eye, and nobody asks what moved it. So the tests here are mostly
about what must *not* move it.
"""

from __future__ import annotations

import math

import pytest

from run_engine.calibration import Prediction, brier, reliability, report, resolve
from run_engine.evidence.ledger import Row
from run_engine.gates import (
    DEFUNDED, FAIL, PASS, PENDING, GateError, Ladder, score_master_metric,
)
from run_engine.probability import (
    Beta, MARKET, betainc, beta_quantile, estimate, observation, priors_from_spec,
    score, thresholds_from_spec,
)
from run_engine.spec.model import GateSpec, MasterMetric, Spec
from run_engine.voi import Experiment, expected_posterior_variance, rank, top_pick


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def gate(gid, cost, alpha=2.0, beta=2.0):
    return GateSpec(id=gid, question=f"does {gid} hold?", cost=cost,
                    prior_alpha=alpha, prior_beta=beta,
                    pass_condition=f"{gid} clears its written threshold")


def a_spec():
    return Spec(
        version=1,
        master_metric=MasterMetric(performance="margin >= 35%", do_no_harm="runway >= 6 months"),
        # Declared deliberately out of cost order, to prove the code sorts them.
        gates=(gate("G2", 200.0), gate("G0", 1_500.0), gate("G3", 50.0), gate("G1", 15_000.0)),
        market_prior=(3.0, 2.0),
    )


def row(topic, value, grade="REAL"):
    return Row(topic=topic, claim=f"{topic} observation", value=value, grade=grade,
               source="CIK: 0000320193", origin="test")


# ---------------------------------------------------------------------------
# Ladder ordering and the defunding rule
# ---------------------------------------------------------------------------


def test_gates_run_cheapest_falsification_first():
    """Invariant 3. The order is computed from the costs, not from the order
    someone happened to type them in."""
    ladder = Ladder.from_spec(a_spec())
    assert ladder.order == ("G3", "G2", "G0", "G1")
    assert ladder.cheapest.id == "G3"
    assert [g.cost for g in ladder.gates] == sorted(g.cost for g in ladder.gates)


def test_a_failed_gate_defunds_everything_downstream():
    """Invariant 4. A red gate stops the spending the same day, and routes back
    to mechanism design rather than filing a note."""
    ladder = Ladder.from_spec(a_spec(), lock_task="T03")
    outcome = ladder.evaluate({"G3": PASS, "G2": FAIL, "G0": PASS, "G1": PASS})

    assert outcome.failed_at == "G2"
    assert outcome.routed_to == "T03"
    # Everything after the failure is defunded regardless of its own result --
    # note G0 and G1 were reported as passing and are still cut off.
    assert outcome.status_of("G0") == DEFUNDED
    assert outcome.status_of("G1") == DEFUNDED
    assert outcome.defunded == ("G0", "G1")
    assert outcome.status_of("G3") == PASS
    assert not outcome.advanced
    assert outcome.blocked
    assert "do not fund" in outcome.to_markdown().lower()


def test_a_gate_nobody_ran_is_pending_not_passed():
    ladder = Ladder.from_spec(a_spec())
    outcome = ladder.evaluate({"G3": PASS})
    assert outcome.status_of("G2") == PENDING
    assert not outcome.advanced, "silence is not a result"


def test_there_is_no_partial_credit():
    ladder = Ladder.from_spec(a_spec())
    three_of_four = ladder.evaluate({"G3": PASS, "G2": PASS, "G0": PASS, "G1": PENDING})
    assert not three_of_four.advanced
    assert "no partial credit" in three_of_four.to_markdown().lower()

    all_four = ladder.evaluate({"G3": PASS, "G2": PASS, "G0": PASS, "G1": PASS})
    assert all_four.advanced


def test_an_unknown_gate_status_is_refused():
    ladder = Ladder.from_spec(a_spec())
    with pytest.raises(GateError, match="unknown status"):
        ladder.evaluate({"G0": "probably fine"})


def test_the_master_metric_is_zero_when_the_do_no_harm_half_is_violated():
    ladder = Ladder.from_spec(a_spec()).evaluate(
        {"G3": PASS, "G2": PASS, "G0": PASS, "G1": PASS})
    healthy = score_master_metric(ladder, do_no_harm_held=True)
    breached = score_master_metric(ladder, do_no_harm_held=False)

    assert healthy.value == pytest.approx(1.0)
    assert breached.value == 0.0, "a plan that wins and kills the company has not won"
    assert "moot" in breached.line()


def test_blocking_lint_findings_degrade_the_metric():
    """A dossier with unsourced numbers cannot score well however good its argument is."""
    ladder = Ladder.from_spec(a_spec()).evaluate(
        {"G3": PASS, "G2": PASS, "G0": PASS, "G1": PASS})
    clean = score_master_metric(ladder, do_no_harm_held=True, lint_blocking=0)
    dirty = score_master_metric(ladder, do_no_harm_held=True, lint_blocking=4)
    assert dirty.value < clean.value


# ---------------------------------------------------------------------------
# The special functions, checked against values that are known independently
# ---------------------------------------------------------------------------


def test_the_incomplete_beta_matches_known_closed_forms():
    # Beta(1,1) is uniform: I_x(1,1) = x
    for x in (0.1, 0.25, 0.5, 0.9):
        assert betainc(1.0, 1.0, x) == pytest.approx(x, abs=1e-9)
    # Beta(2,1) has CDF x^2
    for x in (0.2, 0.5, 0.8):
        assert betainc(2.0, 1.0, x) == pytest.approx(x * x, abs=1e-9)
    # Symmetry: I_x(a,b) = 1 - I_(1-x)(b,a)
    assert betainc(3.0, 7.0, 0.4) == pytest.approx(1.0 - betainc(7.0, 3.0, 0.6), abs=1e-9)


def test_the_quantile_function_inverts_the_cdf():
    for a, b in ((2.0, 5.0), (10.0, 3.0), (1.0, 1.0)):
        for p in (0.1, 0.5, 0.9):
            x = beta_quantile(a, b, p)
            assert betainc(a, b, x) == pytest.approx(p, abs=1e-6)


def test_beta_mean_and_variance_are_the_textbook_values():
    b = Beta(3.0, 7.0)
    assert b.mean == pytest.approx(0.3)
    assert b.variance == pytest.approx(3 * 7 / (100 * 11))
    lo, hi = b.interval(0.8)
    assert 0 < lo < b.mean < hi < 1


# ---------------------------------------------------------------------------
# The rule that makes the number honest
# ---------------------------------------------------------------------------


def test_est_rows_cannot_move_the_number():
    """Invariant 2, applied to arithmetic. EST rows inform debate; they do not
    move the probability, and 'do not move' means bit-identical."""
    priors = priors_from_spec(a_spec())
    base = estimate(priors, [])

    with_est = estimate(priors, [
        row("G0", "pass", grade="EST"),
        row("G0", "40/50", grade="EST"),
        row(MARKET, "pass", grade="EST"),
        row("G1", "fail", grade="WEAK"),
    ])

    assert with_est.mean == base.mean
    assert with_est.lo == base.lo and with_est.hi == base.hi
    assert with_est.real_fraction == base.real_fraction == 0.0
    assert with_est.to_dict() == base.to_dict()


def test_a_real_row_does_move_the_number():
    priors = priors_from_spec(a_spec())
    base = estimate(priors, [])
    moved = estimate(priors, [row("G0", "45/50")])

    assert moved.mean > base.mean, "45 successes out of 50 should raise the estimate"
    assert moved.real_fraction > 0.0

    # The belief that received the evidence must tighten. Note that the *absolute*
    # width of the joint interval grows here, because the mean nearly doubled and a
    # Beta near zero is necessarily narrow -- so the honest check is the variance of
    # the term that was actually informed, plus the relative width of the whole.
    g0_before = next(t for t in base.terms if t.term == "G0")
    g0_after = next(t for t in moved.terms if t.term == "G0")
    assert g0_after.posterior.variance < g0_before.posterior.variance
    assert (moved.hi - moved.lo) / moved.mean < (base.hi - base.lo) / base.mean


def test_an_informational_real_row_is_recorded_but_moves_nothing():
    """'We obtained the tooling quote' is evidence of diligence, not of passing."""
    priors = priors_from_spec(a_spec())
    base = estimate(priors, [])
    noted = estimate(priors, [row("G0", "quote received from factory B")])
    assert noted.mean == base.mean
    assert noted.real_fraction == 0.0


def test_observation_parses_only_the_forms_it_documents():
    assert observation("pass") == (1.0, 0.0)
    assert observation("FAILED") == (0.0, 1.0)
    assert observation("50/900") == (50.0, 850.0)
    assert observation("a promising conversation") is None
    assert observation("900/50") is None, "more successes than trials is not an observation"
    assert observation("") is None


def test_a_declared_threshold_reads_a_ratio_as_a_rate_not_as_trials():
    """The bug this fixes: G0's written pass condition is "at least 25 pre-orders
    from 1,000 qualified clicks". Logged in its own units as "25/1000" and read
    as a Bernoulli sample, a gate that passed *exactly on its written condition*
    dropped the headline from 2.4% to 0.16% -- a fifteenfold penalty for
    succeeding. The ratio was a measured rate all along, and the threshold it
    should be measured against was sitting in the spec as prose.
    """
    # No threshold: a ratio is a sample of trials. "40 of 50 grid cells held" is
    # exactly that, and must keep working.
    assert score("40/50") == (40.0, 10.0)
    assert score("pass") == (1.0, 0.0)

    # With one: the same string is a rate, compared against the target.
    assert score("25/1000", min_rate=0.025) == (1.0, 0.0), "25 per 1,000 clears 25 per 1,000"
    assert score("24/1000", min_rate=0.025) == (0.0, 1.0), "just under is a fail, not a nudge"
    assert score("pass", min_rate=0.025) == (1.0, 0.0), "an explicit verdict still wins"
    assert score("a promising conversation", min_rate=0.025) is None


def test_a_gate_that_passes_on_its_written_condition_does_not_lower_the_headline():
    priors = {"G0": (2, 3), "market": (2, 3)}
    thresholds = {"G0": 0.025}
    base = estimate(priors, [])

    unthresholded = estimate(priors, [row("G0", "25/1000")])
    scored = estimate(priors, [row("G0", "25/1000")], thresholds=thresholds)

    assert unthresholded.mean < base.mean, "the old reading punished a passing gate"
    assert scored.mean > base.mean, "a gate that cleared its condition raises the estimate"
    assert scored.mean == estimate(priors, [row("G0", "pass")]).mean, (
        "a measurement that clears the threshold is worth exactly one pass"
    )


def test_a_gate_without_a_threshold_still_takes_ratios_as_trials(manufacturing_spec=None):
    """G2's "40 of 50 stress-grid cells held" is a genuine Bernoulli sample. The
    fix is per-gate precisely so that this case is left alone."""
    priors = {"G2": (2, 2)}
    assert estimate(priors, [row("G2", "40/50")]).terms[0].posterior == Beta(42.0, 12.0)


def test_the_estimate_is_a_product_of_its_terms_not_an_average():
    priors = {"a": (1.0, 1.0), "b": (1.0, 1.0)}
    est = estimate(priors)
    assert est.mean == pytest.approx(0.25), "0.5 x 0.5, not (0.5 + 0.5) / 2"


def test_the_report_names_three_figures_and_flags_a_guess():
    priors = priors_from_spec(a_spec())
    est = estimate(priors, [])
    line = est.line()
    assert "P(success)" in line and "80% CrI" in line and "REAL fraction" in line
    assert "not a forecast" in est.caveat(), "a number resting on priors must say so"

    evidenced = estimate(priors, [row(g, "90/100") for g in ("G0", "G1", "G2", "G3")])
    assert evidenced.real_fraction > 0.35
    assert "not a forecast" not in evidenced.caveat()


def test_the_interval_brackets_the_mean_and_lies_inside_the_unit_interval():
    priors = priors_from_spec(a_spec())
    for rows in ([], [row("G0", "9/10")], [row(g, "50/60") for g in ("G0", "G1")]):
        est = estimate(priors, rows)
        assert 0.0 <= est.lo <= est.mean <= est.hi <= 1.0
        assert not math.isnan(est.mean)


# ---------------------------------------------------------------------------
# Value of information
# ---------------------------------------------------------------------------


def test_expected_posterior_variance_matches_the_closed_form():
    prior = Beta(2.0, 3.0)
    n = 20
    expected = prior.variance * (prior.alpha + prior.beta) / (prior.alpha + prior.beta + n)
    assert expected_posterior_variance(prior, n) == pytest.approx(expected)
    assert expected_posterior_variance(prior, 0) == prior.variance


def test_voi_ranks_the_cheap_demand_test_above_the_pilot():
    """The gate ordering falls out of the arithmetic rather than being imposed on it."""
    posteriors = {"G0": Beta(2.0, 2.0), "G1": Beta(2.0, 2.0)}
    experiments = [
        Experiment("BP-0.1b", "pilot run of 50 units", "G1", cost=15_000, observations=50),
        Experiment("BP-0.2", "paid demand test at real price", "G0", cost=1_500, observations=900),
    ]
    ranked = rank(experiments, posteriors)
    assert top_pick(ranked).experiment.id == "BP-0.2"
    assert ranked[0].value_per_cost > ranked[1].value_per_cost * 5


def test_an_experiment_against_a_firm_belief_buys_almost_nothing():
    posteriors = {"settled": Beta(400.0, 400.0), "open": Beta(1.0, 1.0)}
    experiments = [
        Experiment("E1", "re-test what we know", "settled", cost=100, observations=20),
        Experiment("E2", "test what we don't", "open", cost=100, observations=20),
    ]
    ranked = rank(experiments, posteriors)
    assert ranked[0].experiment.id == "E2"


def test_an_experiment_informing_an_undeclared_term_is_skipped_not_guessed():
    ranked = rank([Experiment("E1", "x", "nonexistent", cost=10, observations=5)],
                  {"G0": Beta(1.0, 1.0)})
    assert ranked == []


def test_a_free_or_empty_experiment_is_refused():
    with pytest.raises(Exception):
        Experiment("E1", "x", "G0", cost=0, observations=5)
    with pytest.raises(Exception):
        Experiment("E2", "x", "G0", cost=10, observations=0)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def test_brier_is_none_before_anything_resolves():
    """A perfect score from an empty history is the most flattering possible lie."""
    assert brier([Prediction("r1", "G0", 0.7)]) is None


def test_brier_scores_resolved_predictions():
    predictions = [
        Prediction("r1", "G0", 1.0, outcome=1),
        Prediction("r1", "G1", 0.0, outcome=0),
    ]
    assert brier(predictions) == pytest.approx(0.0)

    coin_flips = [Prediction("r1", "G0", 0.5, outcome=1), Prediction("r1", "G1", 0.5, outcome=0)]
    assert brier(coin_flips) == pytest.approx(0.25)


def test_only_a_decided_gate_resolves_a_prediction():
    predictions = [Prediction("r1", g, 0.7) for g in ("G0", "G1", "G2")]
    resolved = resolve(predictions, {"G0": "pass", "G1": "pending", "G2": "defunded"})
    assert [p.outcome for p in resolved] == [1, None, None]


def test_the_calibration_report_calls_out_systematic_optimism():
    predictions = [Prediction("r1", f"G{i}", 0.9, outcome=0) for i in range(6)]
    text = report(predictions)
    assert "Brier score" in text
    assert "optimistic" in text
    assert "amendment" in text, "the correction should be structural, not motivational"


def test_reliability_bins_predictions_against_outcomes():
    predictions = [Prediction("r", "G", 0.9, outcome=1) for _ in range(3)]
    predictions += [Prediction("r", "G", 0.1, outcome=0) for _ in range(3)]
    bands = reliability(predictions, bins=5)
    assert len(bands) == 2
    assert bands[0][3] == pytest.approx(0.0)   # low band, observed 0
    assert bands[-1][3] == pytest.approx(1.0)  # high band, observed 1


def test_dependencies_constrain_the_order_but_cost_still_decides():
    """The near-free analytic gates cannot run first: they have nothing real to
    work on until a paid gate has produced it. Cost decides among what is ready."""
    spec = Spec(
        version=1,
        master_metric=MasterMetric(performance="margin >= 35%", do_no_harm="runway >= 6mo"),
        gates=(
            GateSpec(id="G0", question="will anyone pre-order?", cost=1_500,
                     pass_condition=">= 25 pre-orders"),
            GateSpec(id="G1", question="does it work for real buyers?", cost=15_000,
                     pass_condition="return rate < 8%", depends_on=("G0",)),
            GateSpec(id="G2", question="does it survive the stress grid?", cost=200,
                     pass_condition="metric holds in every cell", depends_on=("G1",)),
            GateSpec(id="G3", question="can we unwind?", cost=500,
                     pass_condition="exit cost below the floor", depends_on=("G2",)),
        ),
    )
    assert Ladder.from_spec(spec).order == ("G0", "G1", "G2", "G3")


def test_a_dependency_cycle_is_refused():
    spec = Spec(
        version=1,
        master_metric=MasterMetric(performance="p", do_no_harm="d"),
        gates=(
            GateSpec(id="A", question="?", cost=1, pass_condition="x", depends_on=("B",)),
            GateSpec(id="B", question="?", cost=1, pass_condition="x", depends_on=("A",)),
        ),
    )
    with pytest.raises(GateError, match="cycle"):
        Ladder.from_spec(spec)


def test_a_dependency_on_a_gate_that_does_not_exist_is_refused():
    spec = Spec(
        version=1,
        master_metric=MasterMetric(performance="p", do_no_harm="d"),
        gates=(GateSpec(id="A", question="?", cost=1, pass_condition="x",
                        depends_on=("NOPE",)),),
    )
    with pytest.raises(GateError, match="not on this ladder"):
        Ladder.from_spec(spec)
