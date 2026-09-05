# The Portable Run Engine

The specification this repository implements, and the map from each part of it to
the code that enforces it.

The design generalizes a decision board built for one scientific programme. Of that
board's sixty nodes, nine were about the subject matter; the other fifty-one were an
**epistemic engine** — a machine for deciding what you are allowed to believe, how much
you are allowed to spend on the strength of that belief, and what has to happen before
you may change your mind. That machine is indifferent to subject matter, which is why
it ports.

An instantiation swaps exactly three things:

- **The substrate** — the state machine of the thing being acted on.
- **The requirement sets and the master metric** — what "good" means here, in numbers.
- **The data layer** — which sources may be read, and which returns count as REAL.

Everything else transfers unchanged.

---

## 1. Five loops and one bias

The engine is not a flowchart. It is five closed loops sharing a spine. If your
instantiation has all five wired, it is the same engine.

| Loop | What it does | Where it lives |
|---|---|---|
| **Run** | One full turn of the crank: board → work chain → finalize → dossier → red team → lint → value of information → continuity | [`engine.py`](../src/run_engine/engine.py) |
| **Spec** | The only way the constitution changes: vote → ratify → changelog → version bump | [`spec/amend.py`](../src/run_engine/spec/amend.py) |
| **Evidence** | Claims become facts, and facts become permission to explore | [`evidence/ledger.py`](../src/run_engine/evidence/ledger.py) |
| **Failure** | A failed gate stops spending and returns to mechanism design | [`gates.py`](../src/run_engine/gates.py) |
| **Substrate** | The thing being studied — the only loop replaced wholesale | [`substrate.py`](../src/run_engine/substrate.py) |

### The bias that makes it work

**Frozen by default.** The Phase 0 gate defaults to *no*. The system refines the
incumbent hypothesis and will not entertain alternatives unless someone passes an
explicit flag or new REAL evidence lands. Exploration is a privilege that evidence
buys.

For a founder or a principal investigator this is the most valuable single import,
because the characteristic failure of planning is the opposite: infinite pivoting on
zero new information. You cannot re-open the model because you read something on a
Tuesday.

Enforced in [`engine.resolve_phase0`](../src/run_engine/engine.py); asserted by
`test_exploration_is_frozen_until_evidence_thaws_it`.

---

## 2. The substrate — the part you replace

Nine stages. Eight describe getting somewhere; the ninth describes getting back.

```
rest → access → convert → intermediate → handling → lock → fire → locked
                                                                     ↓
                                                                    key → rest
```

| Stage | Generic function | Manufacturing | Therapeutic |
|---|---|---|---|
| rest | Default state; stable, safe, returnable | Cash at rest | Untreated disease state |
| access | Reach it without damaging it | Reach the buyer without burning the channel | Reach target tissue at approved exposure |
| convert | The conversion, by means no counterparty can veto | Cash to goods via contract manufacturing | Engage the mechanism |
| intermediate | Valuable, perishable | Finished inventory | Measurable biological response |
| handling | Survives ordinary, uncontrolled conditions | Ordinary fulfilment | Survives ordinary clinical practice |
| lock | Where reversible becomes durable | The sale commits | Durable response off continued dosing |
| fire | One irreversible commitment | The tooling PO | Opening enrolment |
| locked | The achieved permanent state | Recognized revenue | A durable outcome in a cohort |
| **key** | **The mechanism that undoes it** | **The unwind** | **Discontinuation and reversal** |

**The key is mandatory.** A substrate declaring no key raises at load time. A locked
state you cannot deliberately undo is not an achievement; it is a trap you built on
purpose, and in the therapeutic case the trap is made of people.

Asserted by `test_a_substrate_without_a_key_will_not_load`.

---

## 3. Governance

Four artifacts and a roster. Governance does not cascade top-down into the work; it
injects at three separate points — the substrate, the agent grounding, and the review
board — so no single layer can drift alone.

| Artifact | Function | Enforced |
|---|---|---|
| **Brief** | One sentence: the gain, and the thing you must not damage getting it | Both clauses required at load |
| **Contract** | What counts as evidence, what must be tagged a guess, what may never be asserted | Must define what REAL requires |
| **Spec** | The technical constitution. Versioned; changed only by ratified amendment | Two-key amendment path |
| **Master metric** | One scalar, two halves: a performance threshold *and* a do-no-harm constraint | Both halves required at load |
| **Core disciplines** | The minimum disciplines that constitute quorum | Quorum counted in disciplines, not heads |

A brief with one clause is a wish. A performance target without a survival constraint
produces plans that win and kill the company. Both are load errors rather than review
findings, because a review can be rushed.

See [`spec/model.py`](../src/run_engine/spec/model.py).

---

## 4. The work chain

Strictly linear, with three flags that carry real semantics rather than emphasis:

- **`crux`** — the single make-or-break question. Exactly one per chain, tested first.
- **`core`** — if this is wrong, its whole phase is invalid.
- **`finalize`** — where the work leaves its authors. Its owner may own no other task.

In the system this generalizes, these existed only as bold text inside descriptions —
`**THE CRUX.**` in one file, `**This is the FINALIZE task.**` in another. Nothing
parsed them, so nothing could enforce them. Promoting them to schema is most of the
value of [`tasks.py`](../src/run_engine/tasks.py).

The chain length is a property of the domain: the manufacturing pack runs 17 tasks,
the therapeutic pack 12.

---

## 5. The gate ladder

Three requirement sets, one scalar, four gates ordered by **cost of falsification** —
what it costs to find out this fails, not what it costs to pass.

| | Manufacturing | Therapeutic |
|---|---|---|
| **G0** | Will anyone pre-order at the real price? (~$1.5k) | Is there a signal in data that already exists? (~$2k) |
| **G1** | Does it work for real buyers? (~$15k) | Does the mechanism hold at approved exposure? (~$40k) |
| **G2** | Does the metric hold across the stress grid? (~$200) | Does the safety envelope hold in the new population? (~$8k) |
| **G3** | Can the position be unwound, and at what cost? (~$500) | Is the response durable, and can exposure stop? (~$5k) |

Two rules do the work:

**Order by cost of falsification.** Computed from declared costs, not from the order
someone typed them. Note the tension the ladder has to resolve: G2 is nearly free to
*run* and still cannot go first, because it has nothing real to stress until a pilot
has produced invoices. So gates declare dependencies, and the ordering is *among the
gates whose dependencies are placed, always take the cheapest*. With no dependencies
declared this reduces exactly to cost order.

**Failure defunds downstream.** A red gate marks every later gate `defunded` and routes
back to lock design. This is the rule that saves the most money and is broken the most
often, because the day a gate goes red is the day everyone has reasons why the tooling
order should proceed anyway. So it is a routing decision made in code.

There is no partial credit. Three of four gates green produces no evidence.

---

## 6. The evidence discipline

The model is never trusted with the authoritative record.

| | Staging | Promotion |
|---|---|---|
| Who | Agents | A human |
| Default | Writes on request | Preview only |
| Authority | None | Authoritative |
| Requires | Nothing | A resolvable primary-source identifier |

A claim naming a thing in an authoritative register is **REAL**. A claim backed only by
a document that discusses it is **EST**. Neither is **WEAK**, and WEAK is not promotable
at all. No amount of agent confidence moves a row up a grade; only a better identifier
does.

The guarantee is not "agents never write the letters REAL" — it is a text file and
nothing can stop them. The guarantee is that *saying REAL is not how a row becomes
REAL*, which is the only version that can be enforced. See
`test_only_the_promotion_script_writes_a_real_row`.

Full detail: [EVIDENCE-DISCIPLINE.md](EVIDENCE-DISCIPLINE.md).

---

## 7. The data layer

The engine ships no opinion about which databases matter. A pack declares its own and
supplies the keys they need. The rule governing the swap is not *which API* but **which
class of return may be promoted to REAL**:

- **REAL** — a primary record with a retrievable document, or a reproducible query
  against an authoritative register.
- **EST** — everything else: aggregator estimates, scraped inference, analyst reports,
  vendor marketing, and any number a language model produced without a citation.

An aggregator that *estimates* category revenue is EST forever, however confident it
looks. A customs record naming a real shipment is REAL.

The class is a property of the source, declared in `sources.yaml` and looked up by the
engine. `classify()` takes a source name and nothing else — a caller that could propose
a class could propose REAL.

A source whose key is absent reports `needs key` and skips. It does not fabricate, and
it does not silently downgrade itself to EST and carry on. A gap you can see is worth
more than a number you cannot trace.

Some sources are marked `manual`: their terms do not permit automation, so they run as a
human queue. The engine emits the exact search strings, a person runs them, and the
results return through the same staging path with the same identifier requirements.
That is a design position, not an oversight — a pipeline that quietly scrapes a
subscription database produces results its owner cannot publish, cannot cite in a
filing, and cannot defend.

See [`datalayer.py`](../src/run_engine/datalayer.py) and [SOURCES.md](SOURCES.md).

---

## 8. Turning the engine into a probability

Gates are ordered and conditional, so the joint probability is a product, not an
average:

```
P(success) = P(G0) × P(G1|G0) × P(G2|G0,G1) × P(G3|…) × P(market | all gates)
```

- **Prior.** Each gate starts as a Beta prior written into the spec — an EST row,
  argued in the boardroom, versioned like everything else.
- **Update.** Every promoted REAL row bearing on a gate updates it: `Beta(α+s, β+f)`.
  Fifty pre-orders from 900 clicks is not a vibe, it is a posterior.
- **The rule that makes it honest.** *Only REAL rows move the number.* EST rows inform
  debate, set priors and shape the plan; they cannot move the arithmetic. Asserted as
  bit-identity: `test_est_rows_cannot_move_the_number`.
- **Report three figures, never one.** Posterior mean, an 80% credible interval, and
  the **REAL fraction** — the share of the estimate resting on promoted evidence. A
  plan at 62% with a REAL fraction of 0.05 is not a 62% plan; it is a guess with a
  decimal point, and the third figure is what says so.

The product of independent Betas has no closed form. Rather than sample — which would
make runs non-reproducible, and reproducibility is a property this engine sells — the
product's exact mean and variance are computed analytically and moment-matched to a
Beta whose quantiles give the interval. An approximation, deterministic, and documented
at the call site rather than hidden behind a seed.

### Value of information

```
VoI per unit cost = ( Var(term) − E[Var(term | experiment)] ) × stake ÷ cost
```

For a Beta belief updated by n Bernoulli trials this has an exact solution:
`E[Var(θ|X)] = Var(θ)·(a+b)/(a+b+n)`. Two things follow immediately: the first
observations are worth far more than the last, and an experiment against a belief you
already hold firmly buys almost nothing.

This is *why* G0 runs first rather than merely a convention — a $1,500 demand test
collapses more variance per unit spent than a $15,000 pilot, by roughly an order of
magnitude. The ordering falls out of the arithmetic instead of being imposed on it, and
there is a test that asserts exactly that.

### Calibration

Log every gate prediction *before* the gate runs; score it after.
`BS = (1/N) Σ (predicted − outcome)²`. If your 70% predictions come true 40% of the
time, the engine has caught you being optimistic, and the correction is structural: file
an amendment discounting the priors, ratify it, bump the version.

**The honest ceiling.** No engine produces a well-calibrated probability from a standing
start. What this one produces immediately is a number you can *audit*: every term
traceable to a row, every row typed, every type earned by a document. That is worth more
to a lender than a confident forecast, and it is the only version of the number that
survives contact with diligence.

---

## 9. The eight invariants

Remove any one and the system degrades into an ordinary planning document with more
steps. Each has a test named after it, so they appear in the test output as sentences.

| # | Invariant | Test |
|---|---|---|
| 1 | **Frozen by default** — exploration requires a flag or new REAL evidence | `test_exploration_is_frozen_until_evidence_thaws_it` |
| 2 | **Only REAL rows move the number**, and one script performs promotion | `test_only_the_promotion_script_writes_a_real_row`, `test_est_rows_cannot_move_the_number` |
| 3 | **Gates ordered by cost of falsification** | `test_gates_run_cheapest_falsification_first` |
| 4 | **Failure defunds downstream** | `test_a_failed_gate_defunds_everything_downstream` |
| 5 | **Generation and adjudication stay separate** | `test_generation_and_adjudication_never_share_an_actor` |
| 6 | **One master metric, with a do-no-harm clause** | `test_the_master_metric_carries_a_do_no_harm_half` |
| 7 | **Two keys for a spec change** | `test_a_spec_change_requires_a_vote_and_a_ratification` |
| 8 | **Re-ground every agent every run** | `test_every_agent_is_reground_from_artifacts_not_memory` |

---

## 10. Instantiating it for a new goal

In order. Each step produces a file, and the file is the deliverable — not the
intention.

1. **Write the two-clause brief.** The gain and the thing you must not destroy. If you
   cannot name the second clause, you do not yet know what you are risking.
2. **Name the crux.** The single question that makes everything else moot. Write it as
   a falsifiable statement with a number in it.
3. **Draw the substrate state machine.** Nine stages. If you cannot identify the key,
   stop and design one.
4. **Pick the core disciplines.** Five is about right. Fewer and you get capture; more
   and nothing gets decided.
5. **Write the master metric.** One line, two halves, real numbers.
6. **Build the requirement sets.** Three sets, with the headline risk named explicitly
   rather than listed among the others.
7. **Design the gate ladder.** Four gates ordered by cost of falsification, each with a
   written pass condition and a budget. G0 must be cheap enough to run this week.
8. **Wire the data layer.** Each source declares its own evidence class. The agent never
   classifies its own findings.
9. **Set the priors and start logging.** A Beta prior per gate, and a prediction log
   from run one — calibration is only available in hindsight if you wrote it down in
   foresight.
10. **Run it frozen.** Resist thawing for the first several runs. The engine's value
    shows up around run four, when a plateau flag tells you the constraint was never the
    analysis.

`packs/manufacturing` and `packs/therapeutic` are two worked instantiations. Copy the
one closer to your domain and replace its contents.

---

## 11. What this specification does not claim

- It does not make a model honest. It makes a model's dishonesty **visible and
  inert** — a fabricated identifier fails promotion, and an unpromoted row cannot move
  the number.
- It does not produce a calibrated probability on run one. It produces an auditable one,
  and calibration is a measurement you earn over several runs.
- It does not decide anything. Every gate result, every promotion and every ratification
  is an act by a person. The engine's contribution is that those acts are recorded, in
  order, with what was believed at the time.
