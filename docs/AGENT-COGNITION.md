# Where the reasoning comes from

A board of agents is only worth building if its seats think better than one agent asked the
same question. That does not happen by giving them different job titles. This document is
about the layer that actually produces the reasoning — how a seat is constructed, what it
is allowed to do when it does not know something, which model it runs on and why, and where
its facts come from.

[docs/AGENT-PERSONAS.md](AGENT-PERSONAS.md) covers the operating rules every seat receives
and the adversarial pairings between them. This covers the layer underneath: **why a seat
behaves like an experienced specialist rather than a model with a costume on.**

---

## 1. Three sources of a seat's competence

| Source | What it supplies | Failure if missing |
|---|---|---|
| **The charter** | A narrow mandate, a method, and a written line it will not cross | A generalist that agrees with everything |
| **The construction** | A personality with a specific productive bias, and a rule cancelling that bias's known failure | Seventeen seats with one opinion |
| **The grounding** | Retrieved records with resolvable identifiers, graded by where they came from | Fluent recall, unverifiable |

All three are necessary and only the third is checkable by machine. That ordering matters:
the first two shape behaviour and the third is what makes the behaviour worth trusting.

---

## 2. Building a personality from a historic figure

The most useful seats are not "be an expert in X." They are modelled on someone who actually
solved hard problems — and then explicitly stripped of the traits that made that person
difficult to work with.

The method is two-sided, and the second side is the one that does the work:

> **Borrow the trait that made the figure productive. Then attach the specific counter-rule
> for the failure that same trait causes.**

A worked example. Edison is the obvious model for a seat whose job is to keep going when the
literature says a thing cannot be done — and he is also a catalogue of how that disposition
fails.

| Trait borrowed | Why it is worth having | The failure it causes | The counter-rule that cancels it |
|---|---|---|---|
| "Impossible" is merely untested | Stops the board dismissing an unexplored option as a closed one | Overclaiming; announcing results before they exist | **Bold, not delusional.** Possibility fuels the work; evidence governs the claims. Never dress a hope as a result. |
| A dead end is one logged way that won't work | Converts failure into information instead of embarrassment | Brute force — thousands of trials where one good question would do | **Momentum over deadlock.** Name the single cheapest experiment that settles it. |
| 1% inspiration, 99% perspiration | Sustains work through the unglamorous middle | Gatekeeping; hoarding credit; treating collaborators as staff | **Collaborate, don't gatekeep.** Build on the seat before you; hand forward a sharper problem than you received. |
| Total self-belief | Survives the period where nothing works | Straying confidently outside competence | **Stay in your lane, at its edge.** Push your discipline to its limit, then say plainly where its evidence ends. |

Each row is a bias and its antidote, delivered together. A seat given only the left half is a
liability. A seat given only the right half is too cautious to contribute. Delivered as a
pair, the seat reaches — and the reach is bounded by a rule aimed precisely at how that kind
of reaching goes wrong.

The method generalises. Pick the figure whose disposition your seat needs, name what made
them hard to work with, and write the rule that removes it. What you must not do is borrow
the disposition and leave the pathology attached, because a model given a personality will
reproduce the whole of it, including the part where it will not admit a mistake.

### The shared spine

Personality is per-seat. The rules underneath are not. Every seat carries the same three
blocks, word for word:

1. **Operating principles** — evidence over seniority; steelman before countering; a
   disagreement becomes a designed test rather than a deadlock; failure is data and gets
   logged; hold the line only with evidence on the table.
2. **Mindset** — the borrowed-trait block above, with its counter-rules.
3. **An honesty boundary** — a short, explicit list of the claims this project may not
   present as established, whatever the argument around them looks like.

Identical text across the board is what makes seventeen personalities one board instead of
seventeen unrelated prompts. The personalities differ; the standards do not.

---

## 3. Designing the roster: the step that does the most work

Building *one* good personality is the previous section. Building a **board** is a
different job, and it is the one that changes output quality most, because a board's
weakness is almost never that a seat was too weak — it is that seventeen seats turned out
to be the same seat wearing different titles.

The procedure below is what the seat files in a pack are the output of. It is written as
steps because it is genuinely sequential: each one constrains the next.

### Step 1 — Name the irreversible step, then the crux

Before any seat exists, write down two sentences: **what is the decision you cannot take
back**, and **what single unknown decides whether it is a good idea**. Everything else is
downstream. The crux gets a seat of its own and that seat is non-negotiable, because a
roster that spreads attention evenly across a problem with one dominant unknown is a
roster that will study everything except the thing that matters.

### Step 2 — Fix the small non-negotiable core

Most of a board is useful. A few seats are load-bearing, and a run without them is not a
weaker run, it is an invalid one. A workable core is four or five seats answering:

> *make the thing · prove it is safe · make it work in the real world · make it legal ·
> prove the crux*

Mark them in the pack. If one is absent the run should be treated as not having happened,
and the rest of the roster is a matter of resourcing rather than validity.

### Step 3 — Give every seat a discipline, and count quorum in disciplines

Each seat declares `discipline` in the board YAML, and the board declares
`core_disciplines`. Quorum is then computed over **disciplines represented, not heads
present**:

```python
def missing_disciplines(self):
    return [d for d in self.core_disciplines if d not in self.disciplines()]
```

A board of twelve seats who all do the same job is correctly reported as **not quorate**.
`Pack.validate()` raises `PackError` and the pack does not load, so the run never happens
rather than producing a confident consensus from a monoculture:

> *the board does not cover every core discipline; missing: regulatory. A board missing a
> discipline is not quorate and its vote does not count.*

The check is about **coverage, not attendance** — the failure it guards against is a room
that agrees because nobody in it was chartered to raise the objection, which looks exactly
like consensus. It is the cheapest structural defence available against a board that agrees
with itself, and it costs one YAML field per seat.

### Step 4 — Write each personality against the gap the others leave

This is the step people skip, and skipping it is what produces seventeen interchangeable
experts. A seat is not designed in isolation; it is designed **relative to the seats that
already exist**. For each new seat, write three things down before writing the charter:

| Field | The question it answers | Example |
|---|---|---|
| **Archetype** | What kind of thinker is this, in one line? | *the perception realist / calm quantifier* |
| **Complements** | Which seat's weakness does this one cover? | *keeps the inventor's optimism honest by defining the measured target* |
| **Signature** | The one question this seat always asks | *"In what light, measured how? Give me the measurement, not an adjective."* |

If you cannot fill in **Complements** without repeating an existing seat, you do not have
a new seat — you have a duplicate, and adding it makes the board worse by making its
consensus look better-supported than it is.

A roster built this way has a describable shape. A useful throughline:

> the **bold makers** are balanced by the **crux-skeptics**, translated into reality by the
> **quantifiers**, and kept buildable by the **pragmatists**.

Every seat should be placeable in that sentence. A seat you cannot place is usually a seat
you do not need.

### Step 5 — Set temperature against role, and pair it inversely

Temperature is part of the personality, not a tuning knob. The seat chartered to reach runs
hot; the seat chartered to check the reach runs cold. Pair them explicitly with
`challenges`, and pair them **inversely** — two hot seats produce two overreaches and no
check, two cold seats produce agreement and no reach.

The attacker is usually the *colder* of the pair. On the example board the
Reproducibility Reviewer (T=0.4) is chartered to attack the Domain Generalist (T=1.0) —
the coldest seat checks the hottest, because the Generalist is chartered to overreach and
someone has to be chartered to check the reach:

```yaml
- key: domain_generalist
  temperature: 1.0          # chartered to reach; told its errors are anticipated
- key: reproducibility_reviewer
  temperature: 0.4          # chartered to check the reach
  challenges: [domain_generalist]
```

Board validation also refuses an attack that points forward — *you cannot attack an
argument that has not been made yet* — so `challenges` edges only ever run backwards.

### Step 6 — Declare context narrowly, and never let it mean "everything"

Each seat lists exactly the upstream seats it may read. Shared-everything context is worse
than it looks for two reasons: it grows quadratically, so a seventeen-seat board becomes
unaffordable around seat nine — and it destroys the point of having distinct seats, because
once every agent has read every other agent's reasoning they converge, and seventeen
correlated opinions are worth roughly one opinion.

Board validation refuses to load if a `context` or `challenges` reference is misspelled.
That strictness exists because the failure it prevents is **silence**: a seat whose upstream
dependency does not resolve receives an empty context and produces confident, ungrounded
output. Nothing errors. You simply get worse answers, with no signal that anything went
wrong.

### Step 7 — Reweight rather than replace when the problem moves

When the hard part of a problem shifts, the instinct is to swap seats out. Usually the
better move is to **re-center the seats you have** and add only for the genuinely new
unknown — recording, in the pack, that the seat's centre of gravity moved and why. A roster
that is rewritten every time the problem changes loses the thing that made it useful: the
accumulated, written statement of who is responsible for what.

### What each seat file ends up containing

Beyond the shared spine, a fully specified seat declares its mandate, how it thinks, its
methods and standards, how it collaborates, **when it holds the line**, its anti-roadblock
moves, its inputs and outputs, its guardrails, and its signature questions.

The two that carry the most weight are the ones that read oddly at first:

- **When I hold the line** — the conditions under which this seat refuses to agree. Without
  it, a seat's "standards" are aspirational and the model will trade them away under mild
  social pressure from an upstream seat.
- **Anti-roadblock moves** — how this seat unblocks *others*. It is what stops a
  well-designed skeptical seat from becoming a seat that only says no.

### Why this is worth the effort

The design cost is real — it is a day of writing per pack, not an afternoon — so the
argument for it should be explicit.

**Against one capable agent.** A single agent asked to be rigorous is optimizing one
objective, and will satisfy it the cheapest way available: hedging. Hedged text is
unfalsifiable and *reads* as careful, which is what makes it dangerous. Split the objectives
across seats with **opposed incentives** and no single output can satisfy everyone by
hedging — the seat rewarded for reach and the seat rewarded for finding where reach failed
cannot both be satisfied by the safe middle answer.

**Against seventeen generic experts.** Seats that differ only in job title share a
disposition, and shared dispositions produce correlated errors. Seventeen correlated
opinions are one opinion with a false quorum attached — worse than one opinion, because the
apparent agreement is itself read as evidence. Designing each seat against the gap the
others leave is what makes the disagreement real, and **disagreement is the product**: two
competent reviewers reaching different conclusions is information, which is why the dossier
editor is forbidden from resolving it by splitting the difference.

**The failure it prevents is specific.** Not "the board gave a wrong answer" — that is
recoverable and visible. The failure is a board that produces a *confident, unanimous,
well-written* answer that everyone believes because seventeen seats agreed, when in fact one
disposition was consulted seventeen times. Deliberate personality design is the only defence
against that, because none of the downstream machinery can detect it: the grades will be
correct, the citations will resolve, the linter will pass, and the answer will still be the
product of a monoculture.

---

## 4. What a seat does when it does not know

This is the whole anti-hallucination design, and it is a branch with exactly three arms.

```
Fact needed, not in grounding, not in the ledger, not in the knowledge base
    │
    ├── 1. Go and find it       → retrieval, returns records with identifiers
    │                             the finding is STAGED, never self-promoted
    ├── 2. Mark it and move on  → "the context is thin", named as missing,
    │                             carried forward as an open item
    └── 3. Fill the gap from memory
             ✗ not a discouraged option — an absent one
```

Arm 3 is not on the menu. That is the point of the whole architecture: **fabrication is not
a behaviour the system discourages, it is a behaviour with nowhere to go.** A number a model
produced without a citation cannot be graded above EST, and an EST row cannot move the
probability. The row can be written; it simply does nothing.

Arms 1 and 2 are both successes. A seat that reports "I searched these sources, found
nothing, and here is what is missing" has done its job completely. The judge rubric agrees:
`coverage_honesty` scores **zero for silence about gaps**, not a neutral middle — declining
to state coverage is a failure, not an omission.

Being stuck is likewise a state with defined moves rather than a reason to stop. The router
retries a dead end three structural ways before anyone concludes the field is empty, and the
retries are recorded, so "nothing found" from one lazy query is not a reportable result.

---

## 5. Which model runs which seat, and what it costs

Every seat declares a **tier**, not a model. Tiers resolve to model ids through the
environment, so a topology never names a vendor and swapping providers is configuration
rather than a code change.

| Tier | Env var | For |
|---|---|---|
| `heavy` | `MODEL_HEAVY` | Invention, modelling, safety, adjudication — the seats where depth changes the answer |
| `light` | `MODEL_LIGHT` | Structured and advisory seats, where a strong cheaper model is indistinguishable |
| `research` | `MODEL_RESEARCH` | Retrieval-heavy seats working over long source context |
| `outside` | `MODEL_OUTSIDE` | The independence tier — see below |

**Why tiering exists at all.** Assigning every seat the most capable model is the easiest way
to make a multi-agent system indefensibly expensive. A seventeen-seat board run entirely on a
frontier model costs seventeen times what it needs to, and most seats are doing work a
cheaper model does indistinguishably well. Put the best model where the reasoning is hard and
a cheaper one everywhere else, and the run costs a fraction of the naive version with no
measurable loss. Anyone who wants maximum depth sets every tier to the same top model and
pays for it; anyone who wants a cheap pass points them all at a mid-tier model. Neither is a
code change.

### The outside tier is chosen for who trained it, not for how good it is

Every other tier answers "how much capability does this seat need?" This one answers a
different question, and it is the only tier whose value is picked for its provenance.

Two seats running the same model do not disagree independently. They share a training
distribution, so they share what that distribution got wrong — and an adversarial pairing
between them checks style rather than substance, because the attacker cannot see the blind
spot it was trained into.

Running an attacking seat on a **different vendor's** model does not make it smarter. It
makes its errors uncorrelated with its target's, which is the only property that matters when
a seat's entire job is to find what another seat missed. Cross-provider disagreement is then
a signal in its own right: when the outside seat objects, the objection is more likely to be
about the claim than about a shared habit of thought.

`run-engine run --pack <pack> --diverse` routes every seat with a `challenges` edge onto that
tier. The routing is deliberately **targeted rather than global** — moving all seats to
another provider just relocates the monoculture. And if `MODEL_OUTSIDE` is unset the run
degrades loudly: seats keep their declared tier and `00_diversity.md` records that the
attacks were *not* provider-independent. A run that wrongly believes its attacks were
independent is worse than one that knows they were not.

---

## 6. Sources, keys, and why credibility is a property of the source

Seats reach real databases through credentialed connectors. The rule that governs them is not
"which API" but **which class of return may ever be promoted**, and that decision is attached
to the source rather than to the finding.

- **REAL** — a primary record with a retrievable document, or a reproducible query against an
  authoritative register: a government dataset, a regulatory filing, a signed quote, your own
  platform export, a completed test.
- **EST** — everything else: aggregator estimates, scraped inference, analyst reports, vendor
  marketing, and *any* number a language model produced without a citation.

An aggregator estimating a category's size is EST forever, however confident it reads. A
registry record naming a real filing is REAL. The class is declared once, in the pack's
`sources.yaml`, and **the agent is never asked for it** — which is exactly why no amount of
model confidence can move a row up a grade.

A source whose key is absent reports `needs key` and skips. It does not fabricate, and it does
not quietly downgrade itself to EST and carry on. A missing credential is a visible gap in
coverage, and a gap you can see is worth more than a number you cannot trace.

### Ranking credibility you did not earn yourself

If you found the fact, you know what it is worth. If an agent found it, you do not — and that
is the problem this layer exists to solve. Every retrieved record is stamped with an authority
tier by the connector that produced it:

```
primary_db  >  indexed  >  oa  >  weak
```

When duplicates collapse, the highest-authority copy survives and absorbs any identifiers the
weaker copies carried, so nothing is lost by ranking. The result is that a reader can always
answer "how good is this source?" without re-doing the search — the answer travels with the
record.

The same distinction, sharpened, decides the grade at promotion:

| Identifier kind | Example | Grade earned |
|---|---|---|
| **Registry** — names the thing itself | a company id, a patent number, a docket number | REAL |
| **Citation** — names a document discussing the thing | a DOI, a PMID | EST |
| Neither | "our analysis", "industry sources" | not promotable |

A DOI is a real identifier for a real paper, and the paper's claim is still a claim. A
registry id *is* the object. Promotion computes this from the citation rather than reading
the grade a row claims for itself, and it may lower a grade it cannot justify but never
raise one.

### Provenance

Every promoted row carries the identifier it was promoted on, the connector that produced it,
and the run that staged it. Every run seals an archive containing its grounding, one note per
task, the ledger snapshot it was computed against, and its prediction log. Between those two,
any number in a dossier can be walked back to the document it came from, by someone who was
not there when it was found.

That is the whole reason the ledger is a plain text table and the archive is a folder of
Markdown rather than rows in a database: the provenance has to remain legible to a person who
does not have the tool.

---

## 7. The cycle, and why it does not repeat itself

At the end of a run, three things read the output rather than produce it:

1. **The red team** attacks the compiled claim, chartered to refute rather than assess, with
   ties going against the claim.
2. **A hostile reading of the ledger.** An adversary is shown every promoted row and asked one
   question: does the cited identifier actually establish the stated value? It returns
   *identifiers only*. The engine demotes exactly those rows and runs the same arithmetic
   again, producing a second headline. The model names the rows; it is never asked for the
   number, because a probability produced by a model is an opinion wearing a decimal point.
   **The gap between the two headlines is the part of the plan resting on evidence a hostile
   reader would not grant.**
3. **The linter**, which imports nothing that can think, and therefore cannot be argued out
   of a finding.

What survives becomes the continuity block: open items, what is still blocked on what, the
headline, and a plateau flag when several runs in a row have moved neither the number nor the
open-item count — the engine's way of saying the constraint is money or access, not analysis.
That block is grounding for the next run.

### "Deterministic" and "never the same output twice" are not in conflict

Both are true, on different axes, and it is worth being explicit because they sound contradictory:

```
same brief + same spec + same ledger   →  byte-identical output      (auditable)
new evidence / new continuity / amended spec  →  different output    (progress)
```

The **engine** is deterministic: two offline runs of the same pack produce identical
documents, and a test asserts it. That is what makes a run usable as evidence of anything —
if the transcript changed run to run, you could not cite it.

The **input** is what advances. A promoted row, a ratified amendment, a carried-forward open
item: each changes what the next run is grounded on, so the next run reaches somewhere the
last one did not. Progress comes from the ledger and the continuity block growing, never from
sampling noise. A run that produced the same output as its predecessor is telling you
something true — that nothing was learned since — which is precisely what the plateau flag is
for.

---

## What is charter and what is code

The construction described here shapes behaviour. It does not guarantee it, and the
difference is the whole architecture. See the table at the end of
[docs/AGENT-PERSONAS.md](AGENT-PERSONAS.md#what-is-charter-and-what-is-code) — the short
version is that personality, mindset and honesty boundaries are requests made to a language
model, while grade computation, promotion, quarantine and the arithmetic are constraints it
cannot address. The personalities are why the output is good. The code is why it is
trustworthy when the personalities fail.
