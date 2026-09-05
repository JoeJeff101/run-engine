"""Governance: the brief, the spec, the amendment path, the substrate, the chain.

These tests are mostly about *refusals*. Each one names a way a planning system
degrades into an ordinary document with more steps, and asserts that the code
will not let it happen quietly.
"""

from __future__ import annotations

import pytest
import yaml

from run_engine.agents.board import Board, Seat, load_board
from run_engine.spec import Brief, Contract, Spec, SpecError, propose, ratify, vote
from run_engine.spec.model import APPROVED, PENDING, RATIFIED, GateSpec, MasterMetric
from run_engine.substrate import STAGES, Substrate, SubstrateError
from run_engine.tasks import TaskChain, TaskError


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def write(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def a_spec(**overrides):
    base = {
        "version": 1,
        "master_metric": {
            "performance": "contribution margin >= 35% at <= 2,000 units/month",
            "do_no_harm": "cash runway never below 6 months in any stressed cell",
        },
        "core_disciplines": ["manufacturing", "cost", "supply-chain", "regulatory", "demand"],
        "market_prior": {"alpha": 3, "beta": 2},
        "gates": [
            {"id": "G0", "question": "will anyone pre-order at the real price?",
             "cost": 1500, "pass_condition": ">= 25 pre-orders from 1,000 clicks",
             "prior": {"alpha": 2, "beta": 2}},
            {"id": "G1", "question": "does it work for real buyers?",
             "cost": 15000, "pass_condition": "return rate < 8% over 90 days",
             "prior": {"alpha": 3, "beta": 2}},
        ],
        "requirement_sets": [
            {"name": "durability", "requirements": ["input cost", "freight", "FX"],
             "headline_risk": "a fast follower at 60% of our price"},
        ],
    }
    base.update(overrides)
    return base


def a_substrate(drop=None):
    names = {
        "rest": "cash at rest", "access": "reach the buyer", "convert": "cash to goods",
        "intermediate": "finished inventory", "handling": "ordinary fulfilment",
        "lock": "the sale commits", "fire": "the tooling PO",
        "locked": "recognized revenue", "key": "the unwind",
    }
    steps = [{"stage": s, "name": names[s]} for s in STAGES if s != drop]
    return {"subject": "working capital", "steps": steps}


def a_chain(**overrides):
    tasks = [
        {"id": "T01", "title": "map the market", "seat": "analyst", "phase": "1"},
        {"id": "T02", "title": "unit economics", "seat": "cost", "phase": "1", "crux": True},
        {"id": "T03", "title": "lock design", "seat": "strategy", "phase": "1", "core": True},
        {"id": "T04", "title": "verify and sign", "seat": "qa", "phase": "4", "finalize": True},
    ]
    data = {"tasks": overrides.get("tasks", tasks)}
    return data


# ---------------------------------------------------------------------------
# The brief and the master metric: two clauses, always
# ---------------------------------------------------------------------------


def test_a_brief_without_a_do_no_harm_clause_will_not_load(tmp_path):
    write(tmp_path / "brief.yaml", {"gain": "convert cash into sold units"})
    with pytest.raises(SpecError, match="do_no_harm"):
        Brief.load(tmp_path / "brief.yaml")


def test_a_two_clause_brief_loads_and_reads_as_one_sentence(tmp_path):
    write(tmp_path / "brief.yaml",
          {"gain": "convert cash into sold units and back into cash",
           "do_no_harm": "destroying solvency"})
    brief = Brief.load(tmp_path / "brief.yaml")
    assert "without" in brief.one_line()
    assert "solvency" in brief.one_line()


def test_the_master_metric_carries_a_do_no_harm_half():
    """Invariant 6. A performance target without a survival constraint produces
    plans that win and kill the company."""
    with pytest.raises(SpecError, match="do_no_harm"):
        MasterMetric(performance="contribution margin >= 35%", do_no_harm="")

    metric = MasterMetric(performance="margin >= 35%", do_no_harm="runway >= 6 months")
    assert "runway" in metric.one_line()


def test_a_spec_whose_metric_has_one_half_will_not_load(tmp_path):
    data = a_spec()
    data["master_metric"].pop("do_no_harm")
    write(tmp_path / "spec.yaml", data)
    with pytest.raises(SpecError, match="do_no_harm"):
        Spec.load(tmp_path / "spec.yaml")


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_a_contract_states_what_real_requires(tmp_path):
    write(tmp_path / "contract.yaml", {
        "rules": ["No figure enters a document without a source and a REAL/EST tag."],
        "real_requires": "a retrievable primary document or a reproducible authoritative query",
        "never": ["Assert a number a model produced without a citation."],
    })
    contract = Contract.load(tmp_path / "contract.yaml")
    assert "REAL" in contract.as_prompt()
    assert "Never" in contract.as_prompt()


def test_a_contract_with_no_real_definition_is_refused(tmp_path):
    write(tmp_path / "contract.yaml", {"rules": ["be honest"], "real_requires": ""})
    with pytest.raises(SpecError, match="real_requires"):
        Contract.load(tmp_path / "contract.yaml")


# ---------------------------------------------------------------------------
# The spec and its gates
# ---------------------------------------------------------------------------


def test_a_gate_without_a_written_pass_condition_is_refused():
    with pytest.raises(SpecError, match="pass condition"):
        GateSpec(id="G0", question="does anyone want it?", cost=1000, pass_condition="")


def test_spec_loads_gates_priors_and_requirement_sets(tmp_path):
    spec = Spec.load(write(tmp_path / "spec.yaml", a_spec()))
    assert spec.version == 1
    assert [g.id for g in spec.gates] == ["G0", "G1"]
    assert spec.gate("G0").prior_alpha == 2
    assert spec.requirement_sets[0].headline_risk.startswith("a fast follower")


def test_the_spec_digest_names_the_headline_risk_and_orders_gates_by_cost(tmp_path):
    spec = Spec.load(write(tmp_path / "spec.yaml", a_spec()))
    digest = spec.digest()
    assert digest.index("G0") < digest.index("G1")  # cheapest falsification first
    assert "fast follower" in digest


def test_duplicate_gate_ids_are_refused(tmp_path):
    data = a_spec()
    data["gates"][1]["id"] = "G0"
    write(tmp_path / "spec.yaml", data)
    with pytest.raises(SpecError, match="duplicate gate"):
        Spec.load(tmp_path / "spec.yaml")


# ---------------------------------------------------------------------------
# The amendment path: two keys, two people
# ---------------------------------------------------------------------------


def test_a_spec_change_requires_a_vote_and_a_ratification(tmp_path):
    """Invariant 7. A voted amendment still needs an independent ratification
    before it touches the spec, and then the version increments with a diff."""
    spec = Spec.load(write(tmp_path / "spec.yaml", a_spec()))
    amendment = propose(spec, "raise the G0 prior", "gates.G0.prior_alpha", 5.0, by="cost")

    # Key zero: proposing changes nothing.
    assert spec.version == 1
    assert spec.gate("G0").prior_alpha == 2
    assert amendment.status == PENDING

    # A vote alone is not enough, and the engine says so rather than proceeding.
    with pytest.raises(SpecError, match="A vote alone cannot change the spec"):
        ratify(spec, amendment.id, by="qa")

    vote(spec, amendment.id, "demand", "APPROVE")
    vote(spec, amendment.id, "supply-chain", "APPROVE")
    assert amendment.status == APPROVED
    assert spec.version == 1, "an approved amendment must not have landed yet"

    ratify(spec, amendment.id, by="qa")
    assert amendment.status == RATIFIED
    assert spec.version == 2
    assert spec.gate("G0").prior_alpha == 5.0


def test_ratification_writes_a_changelog_entry_with_a_diff(tmp_path):
    spec = Spec.load(write(tmp_path / "spec.yaml", a_spec()))
    amendment = propose(spec, "tighten the margin target", "master_metric.performance",
                        "contribution margin >= 40%", by="cost")
    vote(spec, amendment.id, "demand", "APPROVE")
    ratify(spec, amendment.id, by="qa", now="2026-09-05T00:00:00+00:00")

    entry = spec.changelog[-1]
    assert entry.version == 2
    assert entry.amendment_id == amendment.id
    assert entry.ratified_by == "qa"
    assert "-" in entry.diff and "+" in entry.diff, "a changelog entry without a diff is a claim"
    assert "40%" in entry.diff


def test_the_proposer_cannot_vote_on_or_ratify_their_own_amendment(tmp_path):
    spec = Spec.load(write(tmp_path / "spec.yaml", a_spec()))
    amendment = propose(spec, "cheapen G1", "gates.G1.cost", 9000.0, by="cost")

    with pytest.raises(SpecError, match="cannot also vote"):
        vote(spec, amendment.id, "cost", "APPROVE")

    vote(spec, amendment.id, "demand", "APPROVE")
    with pytest.raises(SpecError, match="cannot ratify"):
        ratify(spec, amendment.id, by="cost")


def test_a_voter_cannot_also_be_the_ratifier(tmp_path):
    """Two keys held by one pair of hands is one key with extra ceremony."""
    spec = Spec.load(write(tmp_path / "spec.yaml", a_spec()))
    amendment = propose(spec, "cheapen G1", "gates.G1.cost", 9000.0, by="cost")
    vote(spec, amendment.id, "demand", "APPROVE")
    with pytest.raises(SpecError, match="independent check"):
        ratify(spec, amendment.id, by="demand")


def test_a_rejected_amendment_cannot_be_ratified(tmp_path):
    spec = Spec.load(write(tmp_path / "spec.yaml", a_spec()))
    amendment = propose(spec, "drop the do-no-harm clause", "master_metric.do_no_harm", "", by="cost")
    vote(spec, amendment.id, "demand", "REJECT")
    vote(spec, amendment.id, "qa", "REJECT")
    with pytest.raises(SpecError, match="rejected"):
        ratify(spec, amendment.id, by="regulatory")


def test_an_amendment_cannot_target_a_path_that_does_not_exist(tmp_path):
    spec = Spec.load(write(tmp_path / "spec.yaml", a_spec()))
    with pytest.raises(SpecError, match="no such gate"):
        propose(spec, "loosen G9", "gates.G9.cost", 1.0, by="cost")
    with pytest.raises(SpecError, match="not amendable"):
        propose(spec, "rewrite history", "changelog.0.summary", "x", by="cost")


def test_a_ratified_spec_round_trips_through_yaml(tmp_path):
    path = write(tmp_path / "spec.yaml", a_spec())
    spec = Spec.load(path)
    amendment = propose(spec, "raise G0 prior", "gates.G0.prior_alpha", 4.0, by="cost")
    vote(spec, amendment.id, "demand", "APPROVE")
    ratify(spec, amendment.id, by="qa")
    spec.save()

    reloaded = Spec.load(path)
    assert reloaded.version == 2
    assert reloaded.gate("G0").prior_alpha == 4.0
    assert reloaded.changelog[-1].amendment_id == amendment.id


# ---------------------------------------------------------------------------
# The substrate: if there is no key, the lock is a trap
# ---------------------------------------------------------------------------


def test_a_substrate_without_a_key_will_not_load(tmp_path):
    write(tmp_path / "substrate.yaml", a_substrate(drop="key"))
    with pytest.raises(SubstrateError, match="the lock is a trap"):
        Substrate.load(tmp_path / "substrate.yaml")


def test_a_substrate_missing_an_ordinary_stage_is_also_refused(tmp_path):
    write(tmp_path / "substrate.yaml", a_substrate(drop="handling"))
    with pytest.raises(SubstrateError, match="missing stage"):
        Substrate.load(tmp_path / "substrate.yaml")


def test_a_complete_substrate_exposes_the_irreversible_step(tmp_path):
    substrate = Substrate.load(write(tmp_path / "substrate.yaml", a_substrate()))
    assert substrate.fire.name == "the tooling PO"
    assert substrate.key.name == "the unwind"
    assert substrate.locked.name == "recognized revenue"
    assert len(substrate.steps) == len(STAGES)


def test_substrate_stages_must_be_declared_in_order(tmp_path):
    data = a_substrate()
    data["steps"][0], data["steps"][5] = data["steps"][5], data["steps"][0]
    write(tmp_path / "substrate.yaml", data)
    with pytest.raises(SubstrateError, match="out of order"):
        Substrate.load(tmp_path / "substrate.yaml")


def test_an_unknown_stage_name_is_refused(tmp_path):
    data = a_substrate()
    data["steps"].append({"stage": "monetize", "name": "??"})
    write(tmp_path / "substrate.yaml", data)
    with pytest.raises(SubstrateError, match="unknown stage"):
        Substrate.load(tmp_path / "substrate.yaml")


# ---------------------------------------------------------------------------
# The work chain
# ---------------------------------------------------------------------------


def test_generation_and_adjudication_never_share_an_actor(tmp_path):
    """Invariant 5. The seat that runs the finalize task may own nothing else."""
    data = a_chain()
    data["tasks"][0]["seat"] = "qa"  # the adjudicator also writes the first task
    write(tmp_path / "tasks.yaml", data)
    with pytest.raises(TaskError, match="Generation and adjudication must not"):
        TaskChain.load(tmp_path / "tasks.yaml")


def test_a_chain_needs_exactly_one_crux(tmp_path):
    data = a_chain()
    data["tasks"][0]["crux"] = True  # now two
    write(tmp_path / "tasks.yaml", data)
    with pytest.raises(TaskError, match="exactly one crux"):
        TaskChain.load(tmp_path / "tasks.yaml")

    data = a_chain()
    data["tasks"][1].pop("crux")  # now none
    write(tmp_path / "tasks.yaml", data)
    with pytest.raises(TaskError, match="exactly one crux"):
        TaskChain.load(tmp_path / "tasks.yaml")


def test_the_finalize_task_must_be_last(tmp_path):
    # T03 signs off, then T04 does more work afterwards -- adjudication that is
    # not the last word is not adjudication.
    data = a_chain(tasks=[
        {"id": "T01", "title": "map the market", "seat": "analyst", "phase": "1"},
        {"id": "T02", "title": "unit economics", "seat": "cost", "phase": "1", "crux": True},
        {"id": "T03", "title": "verify and sign", "seat": "qa", "phase": "4", "finalize": True},
        {"id": "T04", "title": "one more thing", "seat": "strategy", "phase": "4"},
    ])
    write(tmp_path / "tasks.yaml", data)
    with pytest.raises(TaskError, match="must be last"):
        TaskChain.load(tmp_path / "tasks.yaml")


def test_a_valid_chain_exposes_its_crux_core_and_finalize(tmp_path):
    chain = TaskChain.load(write(tmp_path / "tasks.yaml", a_chain()))
    assert chain.crux.id == "T02"
    assert chain.finalize.id == "T04"
    assert [t.id for t in chain.core] == ["T03"]
    assert len(chain.generators) == 3
    assert "[CRUX]" in chain.digest()


def test_a_task_cannot_be_both_the_crux_and_the_check_on_it(tmp_path):
    data = a_chain()
    data["tasks"][1]["finalize"] = True
    write(tmp_path / "tasks.yaml", data)
    with pytest.raises(TaskError, match="cannot be both"):
        TaskChain.load(tmp_path / "tasks.yaml")


def test_tasks_must_be_declared_in_order(tmp_path):
    data = a_chain()
    data["tasks"][0], data["tasks"][1] = data["tasks"][1], data["tasks"][0]
    write(tmp_path / "tasks.yaml", data)
    with pytest.raises(TaskError, match="out of order"):
        TaskChain.load(tmp_path / "tasks.yaml")


# ---------------------------------------------------------------------------
# Quorum by discipline, not headcount
# ---------------------------------------------------------------------------


def _seat(key, discipline=""):
    return Seat(key=key, title=key.title(), phase="1", charter="do the thing",
                discipline=discipline)


def test_a_board_missing_a_core_discipline_is_not_quorate():
    board = Board(
        name="b", subject="s", phases=["1"],
        seats=[_seat("a", "manufacturing"), _seat("b", "cost")],
        core_disciplines=["manufacturing", "cost", "regulatory"],
    )
    assert not board.is_quorate()
    assert board.missing_disciplines() == ["regulatory"]


def test_headcount_does_not_substitute_for_coverage():
    """Twelve people who all do the same job are not a board."""
    board = Board(
        name="b", subject="s", phases=["1"],
        seats=[_seat(f"s{i}", "cost") for i in range(12)],
        core_disciplines=["manufacturing", "cost"],
    )
    assert len(board) == 12
    assert not board.is_quorate()


def test_a_board_covering_every_core_discipline_is_quorate():
    board = Board(
        name="b", subject="s", phases=["1"],
        seats=[_seat("a", "manufacturing"), _seat("b", "cost"), _seat("c", "regulatory")],
        core_disciplines=["manufacturing", "cost", "regulatory"],
    )
    assert board.is_quorate()
    assert board.missing_disciplines() == []


def test_disciplines_survive_a_yaml_round_trip(tmp_path):
    path = tmp_path / "board.yaml"
    write(path, {
        "name": "demo", "subject": "a countertop appliance",
        "phases": ["1"],
        "core_disciplines": ["manufacturing", "cost"],
        "seats": [
            {"key": "mfg", "title": "Manufacturing Engineer", "phase": "1",
             "charter": "route the build", "discipline": "manufacturing"},
            {"key": "cost", "title": "Cost Accountant", "phase": "1",
             "charter": "price the build", "discipline": "cost"},
        ],
    })
    board = load_board(path)
    assert board.is_quorate()
    assert board.seat("mfg").discipline == "manufacturing"
