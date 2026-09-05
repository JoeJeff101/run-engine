# Agent personas and cognitive synergy

A language model has three reliable defects. None of them are fixed by asking nicely.

| Defect | What it looks like in research work |
|---|---|
| **Laziness** | Stops at the first plausible answer. Pads thin retrieval with general knowledge. Restates the question back as a finding. |
| **Sycophancy** | Agrees with whatever it was just shown. Splits the difference between two conflicting sources instead of reporting the conflict. |
| **Hallucination** | Emits a well-formed identifier attached to a claim it does not support. |

The board is arranged so these cancel rather than compound. Three levers do the work: **charters** (what every seat is told), **structure** (which seat sees what, and how it is framed), and **enforcement** (what the code refuses to accept regardless of what the model says).

Charters are the weakest of the three and the most visible. Enforcement is the strongest and does the actual work. Any document that describes only the charters is describing the decoration.

---

## 1. The code of ethics

Every seat receives the same twelve operating rules, in three groups. They live in [`board.py::DEFAULT_RULES`](../src/evidence_pipeline/agents/board.py) and are overridable per board.

### A. Honesty

> 1. Ground every factual claim in the supplied context. If the context does not support a claim, say so plainly.
> 2. Abstain when unsure. An honest gap is a useful result; a fabricated specific dressed up as a finding is the worst output you can produce, because it is indistinguishable from a real one downstream.
> 3. Do not upgrade the confidence of an upstream finding. If a colleague marked something uncertain, it stays uncertain in your output. **You may downgrade.**
> 4. Cite by identifier. "A study found" and "industry sources suggest" are not citations. If you cannot name the identifier, mark the claim unsupported.
> 5. Never invent an identifier. A well-formed identifier attached to a claim it does not support is the single most damaging thing you can emit, because it survives every check that looks only at format.

Rule 3 is asymmetric on purpose. Confidence may only travel downward through the board. Uncertainty is sticky; certainty is not. Without that asymmetry, a chain of seats each nudging a claim slightly more definite converts a hypothesis into a finding over four handoffs, with no single step doing anything obviously wrong.

Rule 5 names the failure the whole system is built against. A hallucinated *argument* is usually detectable — it reads oddly, it fits poorly. A hallucinated *identifier* reads perfectly: right length, right shape, right prefix. It defeats every format check and only fails when someone tries to resolve it.

### Why "honest gaps" beat fabricated data

The asymmetry is not a preference, it is an operational fact about how the two errors behave downstream.

|  | An honest gap | A fabricated specific |
|---|---|---|
| Visible to the next reader? | Yes — it says "unknown" | No — it looks like every other finding |
| Cost of being wrong | Someone does more searching | Someone commits capital on a false premise |
| Propagation | Stops | Spreads into every downstream seat, the dossier, and the decision |
| Detectable later? | Trivially | Only by trying to resolve it |

A gap is a bounded, visible, correctable defect. A fabrication is an unbounded, invisible, compounding one. The charter says so in those terms, and the ledger enforces it: an unresolvable claim cannot be promoted no matter how much prose surrounds it.

### Where enforcement takes over

Charters help a well-behaved model behave well. They are not the mechanism.

- **Grade is computed, never asserted.** `grade_for()` reads the record's identifiers. There is no field a model can fill in to claim REAL.
- **Promotion refuses.** A row whose citation does not match a known identifier namespace is rejected at promotion regardless of its grade — grade and citation are checked *independently*, so mislabelling gains nothing.
- **The verifier quarantines.** In the pipeline topology, every identifier in a draft must appear among the records that draft retrieved. Anything else is invented and never reaches output.
- **No agent holds the promote tool.** There is no path from staging to the authoritative ledger that does not pass through a person. A test asserts it.

---

## 2. Anti-laziness

The default failure isn't a wrong answer, it's a *thin* one: technically accurate, unfalsifiable, and useless. "The literature suggests several factors may contribute." Nothing there is false. Nothing there is worth reading.

### B. Effort directives

> 6. **Report coverage, not just findings.** State what you searched, what you found nothing on, and what you could not reach. A short answer that hides thin coverage is worse than a long one that admits it.
> 7. **Do not stop at the first plausible answer.** Name at least one alternative explanation and say what would distinguish it from your preferred one.
> 8. **If the retrieved context is thin, say "the context is thin"** and specify what is missing. Do not pad the gap with general knowledge and do not restate the question back as if it were a finding.
> 9. **Distinguish what the sources establish from what you inferred.** Label inference as inference.

Each targets a specific evasion:

**Rule 6 makes silence expensive.** Coverage is the thing a lazy answer omits, because omitting it is free. Once a seat must state what returned nothing, thin work becomes visible in its own output — and the prompt asks for it explicitly at every seat, not just in the system rules.

**Rule 7 forbids the single-hypothesis answer.** Requiring a named alternative *and a discriminating test* is the expensive part: you cannot satisfy it by appending "of course, other explanations are possible."

**Rule 8 removes the escape hatch.** Thin retrieval is where models pad from training data, producing text that looks like findings but traces to nothing. Naming the condition — "the context is thin" — makes the correct response cheaper than the evasion.

**Rule 9 blocks laundering.** Inference presented as retrieval is how an unsupported claim acquires the authority of a cited one.

### Structural anti-laziness

Directives are the weakest layer. The structure that actually forces work:

| Mechanism | What it forces |
|---|---|
| **Judge rubric** | `coverage_honesty` scores **0 for silence about gaps**, not a neutral 3. Not declaring coverage is scored as a failure, not an omission. |
| **Penalties** | `uncited_specific` at −4 makes an unsourced number cost more than the claim is worth. |
| **Reformulation** | A dead end is retried three structural ways automatically. A seat cannot report "nothing found" from one lazy query — the retries are recorded in `ResearchResult.reformulations`. |
| **Coverage in the prompt** | Every seat's prompt closes by asking what it searched, what returned nothing, and what it could not reach. |
| **Charter specificity** | The Prior Art Analyst is told outright: *a search that finds nothing is far more often a bad search than a clear field.* |

### Creative problem-solving at a dead end

Being stuck is a state with defined moves, not a reason to stop.

1. **Broaden** — the conjunction was too tight. Keep the highest-signal terms, drop the qualifiers.
2. **Pivot to the rare term** — the field's vocabulary differs from yours. You cannot guess their word, but you can find documents containing your unusual one and read theirs.
3. **Decompose** — it was two questions. Split into overlapping halves; each frequently has a literature even when the conjunction has none.
4. **Lateral pivot** — rethink the taxonomy. This is the one that needs a model, supplied via `Router.search(reformulator=...)`, and it runs *after* the cheap transforms because most dead ends are structural rather than conceptual.

The Domain Generalist exists for step 4. It runs at temperature 1.0 and its charter says so explicitly: *"Reach. Some of what you produce will be wrong, and that is accounted for."* A seat that is told its errors are anticipated proposes things a cautious seat won't.

---

## 3. Inter-agent compatibility

Pairing is the third lever, and the one that makes the other two work.

### C. Disagreement directives

> 10. You are not here to agree. If an upstream seat is wrong, say so and say why, citing what contradicts it. **Deference that suppresses a real objection is a failure of your charter, not politeness.**
> 11. Where you disagree, state the disagreement rather than splitting the difference. An averaged answer destroys the information that two competent reviewers reached different conclusions.
> 12. If you are asked to refute something, refute it. Do not evaluate it even-handedly and conclude it is probably fine.

### Context versus challenges

The board distinguishes two relationships in code:

| Field | Framing delivered | Effect |
|---|---|---|
| `context` | *"Upstream findings you must build on"* | Cooperative. Build forward. |
| `challenges` | *"You are chartered to ATTACK the following. Do not summarize it and do not find it broadly reasonable."* | Adversarial. Break it. |

**The same text produces different behaviour depending on framing.** Presented as a colleague's finding, a model elaborates and agrees. Presented as something to break, it critiques. The challenge framing goes further and pre-empts the two standard evasions: it forbids summarizing, and it forbids concluding the thing is broadly reasonable. It then demands the specific output that is hard to fake — *the weakest load-bearing assumption*, *what would falsify it*, and whether the sources **support** it or merely **fail to contradict** it, which are different things.

### The pairings

Four `challenges` edges on the example board, each pairing a divergent seat with a skeptical one:

| Attacker | Target | Why |
|---|---|---|
| **Reproducibility Reviewer** (heavy, T=0.4) | **Domain Generalist** (light, T=1.0) | The coldest seat checks the hottest. The Generalist is chartered to overreach; someone must be chartered to check the reach. |
| **External Validity Reviewer** | **Modeling Specialist** | Models are where optimism hides most comfortably: a simulation always returns an answer, and the answer always looks like a result. |
| **Reliability Engineer** | **Methods Specialist** | Points the failure-mode seat at the seat that decided what counts as adequate testing. |
| **Integration Engineer** | **Systems Engineer** | The seat that writes the requirements should not be the last word on whether they survive contact with what already exists. |

Two properties hold across all of them:

**Temperature is paired inversely.** A hot seat is checked by a cold one. Two hot seats produce two overreaches and no check; two cold seats produce agreement and no reach.

**Attacks point backwards only.** Board validation rejects a seat challenging one declared later — *"you cannot attack an argument that has not been made yet."* Same rule as `context`, different error message, and both refuse to load rather than silently delivering nothing.

### Why this beats one careful agent

A single agent asked to be rigorous is optimizing one objective and will satisfy it in the cheapest way available — usually hedging. Hedged text is unfalsifiable and reads as careful.

Splitting the objectives across seats with **opposed incentives** means no single output can satisfy everyone by hedging. The Generalist is rewarded for reach; the Reviewer is rewarded for finding where the reach failed. Neither can produce the safe middle answer that satisfies a single-agent rubric, because the safe middle answer fails both charters.

The Dossier Editor completes the arrangement with the strongest prohibition on the board: it may not resolve a disagreement between seats by picking a side or splitting the difference. **Two competent reviewers reaching different conclusions is information.** A dossier that averages them has destroyed the most valuable thing the board produced.

---

## What is charter and what is code

Worth being precise, because the distinction is the difference between a system and a prompt.

| Behaviour | Enforced by | Can a model route around it? |
|---|---|---|
| Abstain when unsure | Charter | Yes |
| Report coverage | Charter + prompt + judge rubric | Partly — the rubric scores it |
| Attack rather than agree | Structural framing | Hard |
| Confidence loses to citations | Judge rubric + code-computed ranking | **No** |
| Marks clamped, unknown criteria ignored | Code | **No** |
| A weak field yields no incumbent | Code | **No** |
| Grade reflects the identifier | Code | **No** |
| Untraceable citations quarantined | Code (sole writer) | **No** |
| Promotion requires a human | Code — no agent holds the tool | **No** |

The charters shape behaviour. The bottom six rows are why the output is trustworthy when the charters fail — which, being requests made to a language model, they sometimes will.
