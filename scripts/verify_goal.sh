#!/usr/bin/env bash
# The goal condition, executable.
#
# This file is the definition of done for the run-engine build. It is written
# before the features it checks, so it starts almost entirely red; turning it
# green is the work. Criteria are commands with expected output rather than
# descriptions, because a description of a passing test is not a passing test.
#
#   bash scripts/verify_goal.sh          # everything, including the cold clone
#   FAST=1 bash scripts/verify_goal.sh   # skip the cold clone while iterating
#
# Exit status is 0 only when every criterion passes. A criterion that cannot be
# evaluated without a credential is reported SKIP and never counted as a pass.
#
# Rules of engagement, for whoever maintains this:
#   - Fix the cause, never the criterion.
#   - A criterion may be changed only if it is genuinely wrong or impossible,
#     and only after saying so out loud.

set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

PASS=0; FAIL=0; SKIP=0
FAILED_IDS=()

if [ -x .venv/bin/python ]; then PY=".venv/bin/python"; else PY="python3"; fi
RE="$PY -m run_engine.cli"

BASELINE_TESTS=137          # measured at commit 43724b7, before any changes

c_pass() { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m  %-4s %s\n' "$1" "$2"; }
c_fail() { FAIL=$((FAIL+1)); FAILED_IDS+=("$1"); printf '  \033[31mFAIL\033[0m  %-4s %s\n' "$1" "$2"; }
c_skip() { SKIP=$((SKIP+1)); printf '  \033[33mSKIP\033[0m  %-4s %s\n' "$1" "$2"; }

# check <id> <description> <command...>  -- passes when the command exits 0
check() {
  local id="$1" desc="$2"; shift 2
  if out=$("$@" 2>&1); then c_pass "$id" "$desc"
  else c_fail "$id" "$desc — $(printf '%s' "$out" | tail -1)"; fi
}

# checksh <id> <description> <shell snippet> -- passes when the snippet exits 0
checksh() {
  local id="$1" desc="$2" snippet="$3"
  if out=$(bash -c "$snippet" 2>&1); then c_pass "$id" "$desc"
  else c_fail "$id" "$desc — $(printf '%s' "$out" | tail -1)"; fi
}

# A pytest selector must both run and match at least one test. A -k expression
# that matches nothing exits 0 in some pytest versions, which would let a
# criterion pass by describing a test that was never written.
checkpytest() {
  local id="$1" desc="$2" selector="$3"
  local out rc
  out=$($PY -m pytest -k "$selector" -p no:cacheprovider 2>&1); rc=$?
  if [ $rc -ne 0 ]; then
    c_fail "$id" "$desc — $(printf '%s' "$out" | grep -E '^(ERROR|FAILED|[0-9]+ failed)' | head -1)"
  elif printf '%s' "$out" | grep -qE '^(no tests ran|.*[^0-9]0 (passed|selected))'; then
    c_fail "$id" "$desc — selector '$selector' matched no tests"
  elif ! printf '%s' "$out" | grep -qE '[1-9][0-9]* passed'; then
    c_fail "$id" "$desc — selector '$selector' matched no tests"
  else
    c_pass "$id" "$desc ($(printf '%s' "$out" | grep -oE '[0-9]+ passed' | head -1))"
  fi
}

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }

RUNTMP="$(mktemp -d)"
trap 'rm -rf "$RUNTMP"' EXIT

printf '\033[1mGOAL CONDITION — run-engine\033[0m\n'
printf 'repo: %s\n' "$ROOT"

# --------------------------------------------------------------------------
section "Suite"
# --------------------------------------------------------------------------

# G1  The full suite is green and nothing was lost in the restructure.
out=$($PY -m pytest -p no:cacheprovider 2>&1)
if printf '%s' "$out" | grep -qE '[0-9]+ failed|error'; then
  c_fail G1 "pytest — $(printf '%s' "$out" | tail -1)"
else
  n=$(printf '%s' "$out" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' | head -1)
  n=${n:-0}
  if [ "$n" -ge "$BASELINE_TESTS" ]; then c_pass G1 "pytest: $n passed (baseline $BASELINE_TESTS)"
  else c_fail G1 "pytest: $n passed, below the $BASELINE_TESTS baseline — tests were lost"; fi
fi

# G2  Every new subsystem is actually exercised by a test, not merely written.
missing=""
for mod in spec substrate tasks gates probability voi calibration runstate lint report engine cli backends; do
  grep -rlq "run_engine[._]$mod" tests/ 2>/dev/null || missing="$missing $mod"
done
if [ -z "$missing" ]; then c_pass G2 "every new subsystem has tests referencing it"
else c_fail G2 "untested subsystems:$missing"; fi

# --------------------------------------------------------------------------
section "The engine runs"
# --------------------------------------------------------------------------

# G3/G4  Both packs execute end to end with no keys and no network.
check G3 "pack A (manufacturing) runs offline" \
  $PY -m run_engine.cli run --pack packs/manufacturing --offline --runs-dir "$RUNTMP/a"
check G4 "pack B (therapeutic) runs offline" \
  $PY -m run_engine.cli run --pack packs/therapeutic --offline --runs-dir "$RUNTMP/b"

# G5  A run folder is complete. Anything missing here means the run loop has a
#     hole in it, even if the process exited 0.
REQUIRED_ARTIFACTS="00_grounding.md 95_value_of_information.md 96_promotion_candidates.md \
97_redteam_verdict.md 98_lint_report.md 99_dossier.md _continuity.md _ledger_snapshot.tsv \
_prediction_log.jsonl run.json report.html"
checksh G5 "run folder carries every required artifact" '
  d=$(ls -d '"$RUNTMP"'/a/*/ 2>/dev/null | tail -1) || exit 1
  [ -n "$d" ] || { echo "no run folder"; exit 1; }
  miss=""
  for f in '"$REQUIRED_ARTIFACTS"'; do [ -f "$d$f" ] || miss="$miss $f"; done
  ls "$d" | grep -qE "^0[1-9]_|^1[0-7]_" || miss="$miss task-notes"
  [ -z "$miss" ] || { echo "missing:$miss"; exit 1; }'

# G6  Two offline runs of the same pack are identical apart from time. If they
#     are not, the transcript cannot be used as evidence of anything.
checksh G6 "offline runs are deterministic" '
  '"$PY"' -m run_engine.cli run --pack packs/manufacturing --offline --runs-dir '"$RUNTMP"'/d1 >/dev/null 2>&1 || exit 1
  '"$PY"' -m run_engine.cli run --pack packs/manufacturing --offline --runs-dir '"$RUNTMP"'/d2 >/dev/null 2>&1 || exit 1
  a=$(ls -d '"$RUNTMP"'/d1/*/ | tail -1); b=$(ls -d '"$RUNTMP"'/d2/*/ | tail -1)
  diff -r -I "[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}T\?[0-9:]*" -I "\"run_id\"" -I "elapsed" "$a" "$b"'

# G7  A sealed run folder is immutable.
checkpytest G7 "finalized run folders reject writes" "run_sealed or RunSealed"

# --------------------------------------------------------------------------
section "The eight invariants"
# --------------------------------------------------------------------------

# G8  Frozen by default: exploration is a privilege evidence buys.
checksh G8a "a flagless run is frozen" '
  d=$(ls -d '"$RUNTMP"'/a/*/ | tail -1); grep -q "\"phase0\": *\"frozen\"" "$d/run.json"'
checksh G8b "--divergence and --attack write their own files" '
  '"$PY"' -m run_engine.cli run --pack packs/manufacturing --offline --divergence --runs-dir '"$RUNTMP"'/dv >/dev/null 2>&1 || exit 1
  '"$PY"' -m run_engine.cli run --pack packs/manufacturing --offline --attack --runs-dir '"$RUNTMP"'/at >/dev/null 2>&1 || exit 1
  [ -f "$(ls -d '"$RUNTMP"'/dv/*/ | tail -1)/00a_divergence.md" ] &&
  [ -f "$(ls -d '"$RUNTMP"'/at/*/ | tail -1)/00b_attack.md" ]'
checkpytest G8c "a new REAL row is the only automatic thaw" "frozen_until_evidence_thaws"

# G9  The model is never trusted with the authoritative record.
checkpytest G9 "promotion is the only path to a REAL row" "only_the_promotion_script_writes_a_real_row"

# G10 A guess cannot move the number.
checkpytest G10 "EST rows cannot move the probability" "est_rows_cannot_move_the_number"

# G12 Cheapest falsification first, and a red gate stops the spending.
checkpytest G12a "gates run cheapest falsification first" "gates_run_cheapest_falsification_first"
checkpytest G12b "a failed gate defunds everything downstream" "failed_gate_defunds_everything_downstream"
checkpytest G12c "the master metric carries a do-no-harm half" "master_metric_carries_a_do_no_harm_half"

# G13 Two keys for a spec change.
checkpytest G13 "a spec change needs a vote and a ratification" "spec_change_requires_a_vote_and_a_ratification"

# G14 Continuity comes from artifacts, never from memory.
checkpytest G14a "every agent is re-grounded from artifacts" "reground_from_artifacts_not_memory"
checkpytest G14b "generation and adjudication never share an actor" "generation_and_adjudication_never_share_an_actor"

# --------------------------------------------------------------------------
section "Arithmetic and adjudication"
# --------------------------------------------------------------------------

# G11 Three figures, never one. A mean alone invites being quoted alone.
checksh G11 "dossier and report state mean, 80% CrI and REAL fraction" '
  d=$(ls -d '"$RUNTMP"'/a/*/ | tail -1)
  grep -qi "80% *\(credible\|CrI\)" "$d/99_dossier.md" &&
  grep -qi "REAL fraction" "$d/99_dossier.md" &&
  grep -qiE "posterior|P\(success\)" "$d/99_dossier.md" &&
  grep -qi "REAL fraction" "$d/report.html"'

# G15 The linter is code, not a judgment call. A validator that can call a model
#     is a validator that can be talked out of its finding.
checkpytest G15a "linter rules fire on malformed dossiers" "lint"
checksh G15b "the linter imports no model backend" '
  [ -f src/run_engine/lint.py ] || { echo "missing src/run_engine/lint.py"; exit 1; }
  ! grep -nE "^(from|import).*(backend|anthropic|openai)" src/run_engine/lint.py'

# G16 Calibration: a prediction logged in foresight, scored in hindsight.
checksh G16 "calibrate reports a Brier score" '
  '"$PY"' -m run_engine.cli calibrate --pack packs/manufacturing --runs-dir '"$RUNTMP"'/a 2>&1 | grep -qi brier'

# --------------------------------------------------------------------------
section "Data layer and live mode"
# --------------------------------------------------------------------------

# G17 The pack declares its own sources; the source assigns its own class.
checksh G17 "sources table lists class and wiring status per source" '
  o=$('"$PY"' -m run_engine.cli sources --pack packs/manufacturing 2>&1) || exit 1
  printf "%s" "$o" | grep -qiE "real|est" &&
  printf "%s" "$o" | grep -qiE "wired|needs key|manual"'

# G18 Live mode is the difference between a demo and a tool. The error path is
#     always checkable; the live path needs Joey's key and is reported honestly
#     as SKIP when absent rather than quietly assumed.
checksh G18a "missing credentials fail loudly and name the variable" '
  env -u ANTHROPIC_API_KEY '"$PY"' -m run_engine.cli run --pack packs/manufacturing --live 2>&1 |
    grep -q ANTHROPIC_API_KEY
  test ${PIPESTATUS[0]:-1} -ne 0 || env -u ANTHROPIC_API_KEY '"$PY"' -m run_engine.cli run \
    --pack packs/manufacturing --live >/dev/null 2>&1; [ $? -ne 0 ]'
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  check G18b "a live run completes against the real API" \
    $PY -m run_engine.cli run --pack packs/manufacturing --live --runs-dir "$RUNTMP/live"
else
  c_skip G18b "live run — ANTHROPIC_API_KEY not set in this shell"
fi
checksh G18c "the suite makes no network calls" 'grep -rq "SourceUnavailable\|monkeypatch\|patch(" tests/conftest.py'

# --------------------------------------------------------------------------
section "Repository"
# --------------------------------------------------------------------------

# G19 Nothing secret, ever.
check   G19a "leak scan clean" $PY scripts/scan_for_leaks.py
checksh G19b ".env is not tracked" '[ "$(git ls-files | grep -c "^\.env$")" = "0" ]'

# G20 Every load-bearing claim in the README points at code or a test.
checksh G20 "docs/CLAIMS.md backs each README claim with a reference" '
  [ -f docs/CLAIMS.md ] || { echo "missing docs/CLAIMS.md"; exit 1; }
  ! grep -qE "TODO|TBD" docs/CLAIMS.md
  grep -cE "\.py:[0-9]+|test_[a-z_]+" docs/CLAIMS.md | grep -qvE "^0$"'

# G21 Published, and the published thing is what is on disk.
checksh G21a "working tree clean" '[ -z "$(git status --porcelain)" ]'
checksh G21b "local and remote HEAD agree" '
  git rev-parse HEAD >/dev/null 2>&1 || exit 1
  r=$(git rev-parse @{u} 2>/dev/null) || { echo "no upstream configured"; exit 1; }
  [ "$(git rev-parse HEAD)" = "$r" ]'

# G22 The only criterion that tests what a reader actually experiences.
if [ "${FAST:-0}" = "1" ]; then
  c_skip G22 "cold clone — skipped (FAST=1)"
else
  checksh G22 "a cold clone installs, tests and runs" '
    set -e
    t=$(mktemp -d)
    git clone -q "'"$ROOT"'" "$t/repo"
    cd "$t/repo"
    python3 -m venv .venv >/dev/null
    .venv/bin/pip install -q -e ".[dev]" >/dev/null
    .venv/bin/python -m pytest -p no:cacheprovider >/dev/null
    .venv/bin/python -m run_engine.cli run --pack packs/manufacturing --offline --runs-dir "$t/runs" >/dev/null
    rm -rf "$t"'
fi

# --------------------------------------------------------------------------
printf '\n\033[1m%s\033[0m\n' "────────────────────────────────────────"
printf 'passed %d   failed %d   skipped %d\n' "$PASS" "$FAIL" "$SKIP"
if [ "$FAIL" -gt 0 ]; then
  printf '\033[31mNOT DONE\033[0m — outstanding: %s\n' "${FAILED_IDS[*]}"
  exit 1
fi
if [ "$SKIP" -gt 0 ]; then
  printf '\033[33mALL CHECKABLE CRITERIA PASS\033[0m — %d skipped, and a skip is not a pass.\n' "$SKIP"
  exit 0
fi
printf '\033[32mGOAL CONDITION MET\033[0m\n'
