# Claims and their backing

Every load-bearing claim the README makes, with the line of code that enforces it or the
test that asserts it.

This file exists because a README is marketing until someone can check it, and because a
project whose whole argument is "evidence, not assertion" should be the first thing held
to that standard. If a row here cannot be verified, the claim should be removed from the
README rather than softened.

Line numbers are accurate as of the commit that last touched this file. Test names are
stable; prefer them when a line has moved.

---

## The evidence discipline

| Claim | Backing |
|---|---|
| "Promotion is a separate command a person runs" | [`evidence/ledger.py:315`](../src/run_engine/evidence/ledger.py) — `promote()`, `apply=False` by default |
| "It refuses any row whose citation does not match a known identifier namespace" | [`evidence/ledger.py:95`](../src/run_engine/evidence/ledger.py) — `PRIMARY_SOURCE_RE`, built from the namespace table |
| "Saying REAL is not how a row becomes REAL" | `test_only_the_promotion_script_writes_a_real_row` — an agent-authored REAL row with no identifier is staged, then rejected at promotion |
| "WEAK is not promotable at all" | [`evidence/ledger.py:103`](../src/run_engine/evidence/ledger.py) — `PROMOTABLE = ("REAL", "EST")`; asserted in `tests/test_dedup_and_ledger.py` |
| "Grade is computed from what resolves, not from what the model asserts" | [`evidence/ledger.py:132`](../src/run_engine/evidence/ledger.py) — `grade_for()` reads the record's identifier, never its prose |

## The arithmetic

| Claim | Backing |
|---|---|
| "Only promoted rows update a gate's posterior" | [`probability.py:290`](../src/run_engine/probability.py) — the single `if grade != "REAL": continue` |
| "Adding an EST row leaves the probability bit-identical" | `test_est_rows_cannot_move_the_number` — compares `mean`, `lo`, `hi` and the full dict |
| "A product, not an average" | `test_the_estimate_is_a_product_of_its_terms_not_an_average` — two 0.5 terms give 0.25 |
| "No SciPy" | [`probability.py:101`](../src/run_engine/probability.py) `betainc`, [`:114`](../src/run_engine/probability.py) `beta_quantile`; `pyproject.toml` lists only `requests` and `PyYAML` |
| "The incomplete beta is correct" | `test_the_incomplete_beta_matches_known_closed_forms` — checked against uniform, x², and the symmetry identity |
| "The interval is obtained by moment-matching rather than sampling" | [`probability.py:167`](../src/run_engine/probability.py) — `_match_beta()`. `probability.py`, `voi.py` and `gates.py` import no RNG. (The package's one `random` import is retry jitter at [`retrieval/router.py:196`](../src/run_engine/retrieval/router.py), which perturbs backoff timing and no output value — the determinism claim is asserted directly by `test_two_offline_runs_produce_identical_documents` rather than inferred from imports.) |
| "Two runs of the same pack are byte-identical" | `test_two_offline_runs_produce_identical_documents`; also criterion **G6** in `scripts/verify_goal.sh` |
| "REAL fraction is the share resting on promoted evidence" | [`probability.py`](../src/run_engine/probability.py) — `observed / (observed + prior_mass)` in `estimate()` |
| "A low REAL fraction says so in words" | `test_the_report_names_three_figures_and_flags_a_guess` — asserts "not a forecast" appears |
| "VoI has an exact closed form" | [`voi.py:85`](../src/run_engine/voi.py) — `expected_posterior_variance`; verified against the algebra in `test_expected_posterior_variance_matches_the_closed_form` |
| "The cheap demand test collapses ~10× more variance per unit spent" | `test_voi_ranks_the_cheap_demand_test_above_the_pilot` — asserts a factor of 5 or better, on the packs' real numbers |

## The eight invariants

| # | Claim | Backing |
|---|---|---|
| 1 | Frozen by default | [`engine.py:142`](../src/run_engine/engine.py) `resolve_phase0`; `test_exploration_is_frozen_until_evidence_thaws_it` |
| 1 | An EST row does not thaw the freeze | `test_an_est_row_does_not_thaw_the_freeze` |
| 1 | `--attack` and `--divergence` write their own files | `test_attack_and_divergence_are_explicit_and_write_their_own_files`; criterion **G8b** |
| 2 | Only REAL rows move the number | see *The arithmetic* above |
| 3 | Gates ordered by cost of falsification | [`gates.py:111`](../src/run_engine/gates.py) `_order()`; `test_gates_run_cheapest_falsification_first` |
| 3 | "…subject only to what each gate needs to exist before it" | `test_dependencies_constrain_the_order_but_cost_still_decides` |
| 4 | Failure defunds downstream | [`gates.py:197`](../src/run_engine/gates.py); `test_a_failed_gate_defunds_everything_downstream` |
| 4 | Even a gate reported as passing is cut off behind a failure | same test — G0 and G1 report `pass` and are still `defunded` |
| 5 | The finalize seat owns no other task | [`tasks.py:113`](../src/run_engine/tasks.py); `test_generation_and_adjudication_never_share_an_actor` |
| 6 | The master metric needs both halves | [`spec/model.py:156`](../src/run_engine/spec/model.py); `test_the_master_metric_carries_a_do_no_harm_half` |
| 6 | A violated do-no-harm half scores zero | `test_the_master_metric_is_zero_when_the_do_no_harm_half_is_violated` |
| 7 | Two keys for a spec change | [`spec/amend.py:88`](../src/run_engine/spec/amend.py) `ratify()`; `test_a_spec_change_requires_a_vote_and_a_ratification` |
| 7 | The proposer may not vote or ratify; a voter may not ratify | `test_the_proposer_cannot_vote_on_or_ratify_their_own_amendment`, `test_a_voter_cannot_also_be_the_ratifier` |
| 7 | "A version bump with a diff. Never an edit." | `test_ratification_writes_a_changelog_entry_with_a_diff` |
| 8 | Re-ground every agent every run | `test_every_agent_is_reground_from_artifacts_not_memory` |
| 8 | A ratified change reaches grounding immediately | `test_grounding_reflects_a_ratified_spec_change_immediately` |

## Governance and structure

| Claim | Backing |
|---|---|
| "A brief with one clause is a wish" | [`spec/model.py:68`](../src/run_engine/spec/model.py); `test_a_brief_without_a_do_no_harm_clause_will_not_load` |
| "A substrate declaring no key raises at load time" | [`substrate.py:96`](../src/run_engine/substrate.py); `test_a_substrate_without_a_key_will_not_load` |
| "Quorum counted in disciplines, not heads" | [`agents/board.py:85`](../src/run_engine/agents/board.py) `is_quorate()`; `test_headcount_does_not_substitute_for_coverage` |
| "Exactly one crux per chain" | `test_a_chain_needs_exactly_one_crux` |
| "A task owned by a seat not on the board is a load error" | `test_a_pack_whose_task_names_an_absent_seat_is_refused` |
| "The plateau flag" | [`runstate.py:124`](../src/run_engine/runstate.py) `detect_plateau`; `test_three_flat_runs_with_no_shrinking_open_items_is_a_plateau` |
| "An archive that can be revised after the fact is a draft" | [`runstate.py:179`](../src/run_engine/runstate.py) seal check; `test_a_sealed_run_folder_refuses_further_writes` |
| "Failed runs are archived too" | `test_failed_runs_are_archived_too` |

## The data layer

| Claim | Backing |
|---|---|
| "The class is a property of the source" | [`datalayer.py:115`](../src/run_engine/datalayer.py) — `classify()` takes a source name and nothing else |
| "A caller that could propose a class could propose REAL" | `test_the_class_comes_from_the_source_not_the_caller`, `test_an_undeclared_source_cannot_be_classified_at_all` |
| "There is no third class" | `test_a_source_declaring_a_third_class_is_refused` |
| "A missing key makes a source skip and say so" | [`datalayer.py:77`](../src/run_engine/datalayer.py) `status()`; `test_a_missing_key_makes_a_source_skip_and_say_so` |
| "It does not silently downgrade itself to EST" | same test — the class is unchanged when the credential is absent |
| "Sources whose terms forbid automation run as a human queue" | `manual: true` in both packs' `sources.yaml`; `test_a_manual_source_is_a_human_queue_not_a_failure` |

## The linter

| Claim | Backing |
|---|---|
| "Deterministic, imports nothing that can think" | `test_the_linter_imports_nothing_that_can_think`; criterion **G15b** |
| "The engine's own dossier passes the engine's own linter" | `test_the_engines_own_dossier_passes_the_engines_own_linter` |
| "Three linter false positives found by running it on real output" | Fixed and commented at [`lint.py:43`](../src/run_engine/lint.py) (bare `per`), [`lint.py:65`](../src/run_engine/lint.py) (`IDENT_TOKEN`), and the `_table_blocks` docstring (separator-row splitting). Regression tests: `test_lint_accepts_a_figure_that_carries_a_source_or_a_tag`, `test_lint_does_not_mistake_an_identifier_for_a_quantity`, `test_lint_accepts_a_total_that_reconciles` |
| "Suppression is visible and counted" | [`lint.py:128`](../src/run_engine/lint.py) `LINT_OFF`; the count is printed in every lint report |

## Live mode

| Claim | Backing |
|---|---|
| "`--offline` is the default; `--live` is a deliberate opt-in" | `test_offline_is_the_default_and_live_is_an_opt_in` |
| "It says so precisely if the key is missing" | [`agents/backends/anthropic.py:63`](../src/run_engine/agents/backends/anthropic.py); `test_a_live_run_without_a_credential_refuses_and_names_the_variable`; criterion **G18a** |
| "A topology never names a vendor model" | `test_the_model_id_comes_from_the_environment_when_set` — tier → env var → model id |
| "The call cap is a real ceiling" | `test_the_call_cap_is_a_real_ceiling` |
| "Zero network calls in the suite" | The live backend is tested against an injected client; `tests/conftest.py` patches the HTTP layer to raise; criterion **G18c** |

## The packs

| Claim | Backing |
|---|---|
| "17 seats / 17 tasks" (manufacturing) | `packs/manufacturing/board.yaml`, `tasks.yaml` — counted at load |
| "11 seats / 12 tasks" (therapeutic) | `packs/therapeutic/board.yaml`, `tasks.yaml` |
| "They share no vocabulary and run through identical code" | `test_run_command_executes_and_reports` runs the therapeutic pack through the same `engine.run`; criteria **G3** and **G4** |
| "Both ship with an empty evidence ledger, on purpose" | Neither pack contains `evidence.md`; every run reports `REAL fraction 0.00` |
| "Every source in both packs declares a class and what it answers" | `test_both_shipped_packs_declare_a_class_for_every_source` |

## Numbers quoted in the README

| Number | How to reproduce |
|---|---|
| `270 passed` | `pytest` |
| `P(success) = 2.4% (80% CrI 0.1%–6.2%) · REAL fraction 0.00` | `run-engine run --pack packs/manufacturing` on a pack with no evidence |
| The run-folder listing | `ls runs/<latest>/` after any run |

---

## Known gaps

Stated here rather than omitted.

- **The live path is exercised, not proven at scale.** `--live` is tested against a fake
  client and has been run against the real API by hand. There is no recorded
  long-running live evaluation, and the README does not claim one.
- **Calibration has no history yet.** The prediction log is written from run one, but no
  gate in either shipped pack has resolved, so the Brier score is correctly reported as
  unavailable rather than as a number.
- **Retrieval adapters outnumber wired pack sources.** Both packs declare more sources
  than this repository automates; the unautomated ones are marked `needs key`, `manual`
  or `not wired` and appear that way in `run-engine sources`.
