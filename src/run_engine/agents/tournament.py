"""Topology 2: temperature-diverged fan-out, then a judge.

When the question is "which of several approaches is right?", asking one agent
produces one answer that sounds equally confident whether or not it considered
the alternatives. The fix is to make the alternatives argue.

N champion agents each advocate for a different approach, **each instantiated at
a different sampling temperature**. The temperature spread is the part people
skip, and it is what makes this work. Run identical prompts at identical
temperature and you get N paraphrases of the same answer -- the appearance of
deliberation with none of the substance. Spread the temperature and the low-temp
champions produce careful conventional arguments while the high-temp ones
produce genuinely different proposals, some of which are bad. Bad proposals are
useful: they give the judge something to reject, which is how you can tell the
judge is discriminating rather than rubber-stamping.

The judge then emits a ledger classifying each option as INCUMBENT, FALLBACK, or
DEAD-END, and that ledger **persists across runs**. A later run does not start
fresh: it inherits the standing verdict and must either defend it with new
evidence or mount a real challenge. Without persistence, successive runs
rediscover and re-litigate the same dead ends forever, which is expensive and
feels like progress.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .backend import LLMBackend

STATUSES = ("INCUMBENT", "FALLBACK", "DEAD-END")

# ---------------------------------------------------------------------------
# The rubric
# ---------------------------------------------------------------------------
# The judge scores criteria. The *code* computes the ranking. That split is the
# whole point: a judge asked "which is best?" rewards the most confident-sounding
# champion, because confidence is the most salient signal in the text. A judge
# asked "how many load-bearing claims carry a resolvable identifier?" has to go
# and count.
#
# Weights are deliberately lopsided toward evidence density. An argument that is
# elegant, specific, falsifiable, and uncited should lose to a plodding one with
# citations, because the first is a hypothesis and the second is a finding.


@dataclass(frozen=True)
class Criterion:
    key: str
    label: str
    weight: int
    guidance: str


@dataclass(frozen=True)
class Penalty:
    key: str
    points: int
    description: str


CRITERIA: tuple[Criterion, ...] = (
    Criterion("evidence_density", "Evidence density", 3,
              "0-5. What fraction of load-bearing claims carry a resolvable "
              "identifier? Count them. 5 = every one; 0 = none."),
    Criterion("specificity", "Specificity", 2,
              "0-5. Does it name concrete mechanisms, quantities, parties, and "
              "conditions, or does it gesture at categories?"),
    Criterion("falsifiability", "Falsifiability", 2,
              "0-5. Does it state what observation would prove it wrong? An "
              "unfalsifiable proposal scores 0 regardless of how plausible."),
    Criterion("coverage_honesty", "Coverage honesty", 1,
              "0-5. Does it declare what it could not find or could not reach? "
              "Silence about gaps scores 0, not 3."),
)

PENALTIES: tuple[Penalty, ...] = (
    Penalty("logical_leap", -3,
            "a conclusion that does not follow from the evidence cited for it"),
    Penalty("uncited_specific", -4,
            "a specific number, name, or date presented without an identifier"),
    Penalty("confidence_substitution", -2,
            "assertive language standing in for evidence ('clearly', 'well "
            "established', 'it is widely accepted')"),
    Penalty("unfalsifiable_claim", -2,
            "a claim constructed so that no observation could contradict it"),
)

MAX_SCORE = sum(c.weight * 5 for c in CRITERIA)   # 40
DEAD_END_FLOOR = 0.35 * MAX_SCORE                 # below this, not worth carrying


def rubric_text() -> str:
    """The rubric, rendered for the judge prompt."""
    lines = ["Score each option on every criterion. Weights are fixed:"]
    for c in CRITERIA:
        lines.append(f"  {c.key} (weight {c.weight}) -- {c.guidance}")
    lines.append("")
    lines.append("Then list any penalties that apply, by key:")
    for p in PENALTIES:
        lines.append(f"  {p.key} ({p.points}) -- {p.description}")
    return "\n".join(lines)

# A spread, not a list of preferences. Index into it by champion position; the
# last entry is deliberately above 1.0 to guarantee at least one genuinely
# divergent proposal.
TEMPERATURE_SPREAD = (0.6, 0.8, 0.9, 0.95, 1.1)

_LEDGER_BLOCK = re.compile(r"<<LEDGER>>(.*?)<<END>>", re.DOTALL)


@dataclass
class Option:
    key: str
    description: str


@dataclass
class Proposal:
    option: str
    temperature: float
    text: str


@dataclass
class Score:
    option: str
    marks: dict[str, int] = field(default_factory=dict)
    penalties: list[str] = field(default_factory=list)
    note: str = ""

    def weighted(self) -> int:
        return sum(
            max(0, min(5, self.marks.get(c.key, 0))) * c.weight for c in CRITERIA
        )

    def penalty_points(self) -> int:
        lookup = {p.key: p.points for p in PENALTIES}
        return sum(lookup.get(k, 0) for k in self.penalties)

    def total(self) -> int:
        return self.weighted() + self.penalty_points()

    def breakdown(self) -> str:
        parts = [f"{c.key}={self.marks.get(c.key, 0)}x{c.weight}" for c in CRITERIA]
        pen = f" penalties={','.join(self.penalties)}({self.penalty_points()})" if self.penalties else ""
        return f"{' '.join(parts)}{pen} -> {self.total()}/{MAX_SCORE}"


@dataclass
class Verdict:
    option: str
    status: str
    rationale: str = ""
    score: Score | None = None


@dataclass
class TournamentResult:
    question: str
    proposals: list[Proposal] = field(default_factory=list)
    verdicts: list[Verdict] = field(default_factory=list)
    scores: list[Score] = field(default_factory=list)
    raw_judgment: str = ""
    inherited: list[Verdict] = field(default_factory=list)
    scorecard_parsed: bool = True

    def incumbent(self) -> Verdict | None:
        return next((v for v in self.verdicts if v.status == "INCUMBENT"), None)

    def render(self) -> str:
        lines = [f"Tournament: {self.question}", ""]
        for prop in self.proposals:
            lines.append(f"  champion {prop.option} @ T={prop.temperature:.2f}")
        lines.append("")
        if not self.scorecard_parsed:
            lines.append("  (judge did not produce a readable scorecard)")
        for verdict in self.verdicts:
            lines.append(f"  {verdict.status:<10} {verdict.option:<14} {verdict.rationale}")
        if self.verdicts and not self.incumbent():
            lines.append("")
            lines.append("  No incumbent: every option scored below the floor.")
        return "\n".join(lines)


_SCORES_BLOCK = re.compile(r"<<SCORES>>(.*?)<<END>>", re.DOTALL)


def parse_scores(text: str) -> list[Score]:
    """Parse the judge's scorecard.

    Row format: ``option | k=v k=v ... | penalty,penalty | note``
    Malformed rows are dropped rather than guessed at -- a scorecard you cannot
    read is not a scorecard you should act on.
    """
    match = _SCORES_BLOCK.search(text or "")
    if not match:
        return []
    scores: list[Score] = []
    valid_keys = {c.key for c in CRITERIA}
    valid_penalties = {p.key for p in PENALTIES}

    for line in match.group(1).splitlines():
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        option = parts[0]
        if not option or option.lower() == "option":
            continue
        marks: dict[str, int] = {}
        for token in (parts[1] if len(parts) > 1 else "").replace(",", " ").split():
            if "=" not in token:
                continue
            key, _, raw = token.partition("=")
            if key.strip() in valid_keys:
                try:
                    marks[key.strip()] = int(float(raw))
                except ValueError:
                    continue
        penalties = [
            p.strip()
            for p in (parts[2] if len(parts) > 2 else "").replace(";", ",").split(",")
            if p.strip() in valid_penalties
        ]
        if not marks:
            continue
        scores.append(
            Score(option=option, marks=marks, penalties=penalties,
                  note=parts[3] if len(parts) > 3 else "")
        )
    return scores


def classify(scores: list[Score]) -> list[Verdict]:
    """Turn scores into statuses. Computed here, never taken from the judge.

    Ranking is by total, then by evidence density, then by fewest penalties.
    Evidence density breaks ties on purpose: when two options score the same
    overall, the one whose claims are actually citable wins.

    Exactly one INCUMBENT. Anything below the dead-end floor is dropped rather
    than kept as a fallback -- carrying a weak option forward makes later runs
    re-litigate it, which is expensive and feels like progress.
    """
    if not scores:
        return []

    ranked = sorted(
        scores,
        key=lambda s: (-s.total(), -s.marks.get("evidence_density", 0), len(s.penalties)),
    )
    verdicts: list[Verdict] = []
    for index, score in enumerate(ranked):
        if index == 0 and score.total() >= DEAD_END_FLOOR:
            status = "INCUMBENT"
        elif score.total() >= DEAD_END_FLOOR:
            status = "FALLBACK"
        else:
            status = "DEAD-END"
        verdicts.append(
            Verdict(option=score.option, status=status,
                    rationale=score.breakdown(), score=score)
        )

    # Every option scored below the floor: there is no incumbent. Say so rather
    # than promoting the least-bad option, which would launder a weak field into
    # a decision.
    if all(v.status == "DEAD-END" for v in verdicts):
        return verdicts
    return verdicts


def parse_ledger(text: str) -> list[Verdict]:
    """Extract the judge's ledger. Malformed rows are dropped, not guessed at."""
    match = _LEDGER_BLOCK.search(text or "")
    body = match.group(1) if match else (text or "")
    verdicts: list[Verdict] = []
    for line in body.splitlines():
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            continue
        status = parts[1].upper().replace("_", "-")
        if status not in STATUSES:
            continue
        verdicts.append(
            Verdict(option=parts[0], status=status, rationale=parts[2] if len(parts) > 2 else "")
        )
    return verdicts


def load_standing(path: str | Path | None) -> list[Verdict]:
    """Read the standing ledger. A corrupt file is an empty ledger, not a crash."""
    if not path:
        return []
    p = Path(path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [
            Verdict(
                option=item.get("option", ""),
                status=item.get("status", ""),
                rationale=item.get("rationale", ""),
            )
            for item in data
        ]
    except (OSError, ValueError, TypeError, AttributeError):
        return []


def save_standing(path: str | Path | None, verdicts: list[Verdict]) -> None:
    """Persist statuses only. The scorecard belongs to the run that produced it.

    A later judge inherits *what was decided*, not the numbers behind it --
    otherwise it anchors on a previous judge's marks instead of reading the
    proposals in front of it.
    """
    if not path or not verdicts:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            [
                {
                    "option": v.option,
                    "status": v.status,
                    # The judge's prose note, never the numeric breakdown.
                    # Carrying marks forward would anchor the next judge on a
                    # previous one's scoring instead of the proposals in front
                    # of it -- and the marks were about last run's proposals,
                    # not this run's.
                    "rationale": (v.score.note if v.score else v.rationale),
                }
                for v in verdicts
            ],
            indent=2,
        ),
        encoding="utf-8",
    )


def run_tournament(
    question: str,
    options: list[Option],
    backend: LLMBackend,
    context: str = "",
    standing_path: str | Path | None = None,
    judge_tier: str = "heavy",
    max_tokens: int = 2048,
) -> TournamentResult:
    """Fan out champions at spread temperatures, then judge."""
    inherited = load_standing(standing_path)
    result = TournamentResult(question=question, inherited=inherited)

    for index, option in enumerate(options):
        temperature = TEMPERATURE_SPREAD[min(index, len(TEMPERATURE_SPREAD) - 1)]
        system = (
            f"You are the champion for one candidate approach: {option.key}.\n"
            f"{option.description}\n\n"
            "Argue for it as strongly as the evidence honestly allows, and state "
            "plainly where the evidence does not support it. Do not argue for the "
            "other approaches; other champions have that job."
        )
        text = backend.complete(
            system=system,
            prompt=f"{context}\n\nQuestion: {question}\n\nMake your case.",
            tier="light",
            temperature=temperature,
            max_tokens=max_tokens,
        )
        result.proposals.append(Proposal(option=option.key, temperature=temperature, text=text))

    standing_note = ""
    if inherited:
        rows = "\n".join(f"{v.option} | {v.status} | {v.rationale}" for v in inherited)
        standing_note = (
            "A previous run left this standing ledger. You may not silently "
            "overturn it: to change a status you must cite new evidence that the "
            "previous run did not have.\n\n" + rows + "\n\n"
        )

    proposals_block = "\n\n".join(
        f"--- Champion {p.option} (T={p.temperature:.2f}) ---\n{p.text}" for p in result.proposals
    )
    judgment = backend.complete(
        system=(
            "You are the judge. You did not author any of these proposals and owe "
            "none of them loyalty.\n\n"
            "You do not pick a winner. You score each option against a fixed "
            "rubric, and the ranking is computed from your scores. Do not reward "
            "an option for sounding certain -- assertiveness is not evidence, and "
            "there is a penalty for it. Count identifiers; do not estimate them."
        ),
        prompt=(
            f"{standing_note}{proposals_block}\n\n"
            f"{rubric_text()}\n\n"
            "Emit your scorecard between <<SCORES>> and <<END>>, one row per "
            "option, formatted:\n"
            "  option | evidence_density=N specificity=N falsifiability=N "
            "coverage_honesty=N | penalty_key,penalty_key | one-line note\n"
            "Leave the penalty field empty if none apply.\n"
            "RESPOND_WITH: scores"
        ),
        tier=judge_tier,
        temperature=0.2,
        max_tokens=max_tokens,
    )
    result.raw_judgment = judgment
    result.scores = parse_scores(judgment)

    if result.scores:
        result.verdicts = classify(result.scores)
    else:
        # The judge did not produce a readable scorecard. Fall back to a plain
        # status ledger rather than discarding the round -- but the fallback is
        # recorded, because a judge that cannot follow the rubric is a signal.
        result.verdicts = parse_ledger(judgment)
        result.scorecard_parsed = False

    save_standing(standing_path, result.verdicts)
    return result


def red_team(
    claim: str,
    backend: LLMBackend,
    context: str = "",
    voters: int = 3,
    tier: str = "heavy",
) -> dict[str, Any]:
    """Adversarial verification: several independent attempts to refute a claim.

    Each voter is told to *refute*, not to evaluate. Asking "is this correct?"
    reliably yields agreement, because agreement is the path of least
    resistance for a model that has just been shown a confident assertion.
    Asking "find what is wrong with this" produces genuinely different behaviour.

    A claim survives on a majority of failed refutations, and ties go against
    the claim.
    """
    refuted = 0
    notes: list[str] = []
    for i in range(voters):
        response = backend.complete(
            system=(
                "You are an adversarial reviewer. Your job is to refute the claim "
                "below, not to agree with it. If the evidence is insufficient to "
                "establish the claim, that is a refutation. Default to refuted "
                "when uncertain."
            ),
            prompt=(
                f"{context}\n\nClaim: {claim}\n\n"
                f"Attempt {i + 1}: find the strongest reason this claim fails.\n"
                "RESPOND_WITH: verdict"
            ),
            tier=tier,
            temperature=0.2 + 0.2 * i,
            max_tokens=1024,
        )
        notes.append(response)
        if "UNSUPPORTED" in response.upper():
            refuted += 1

    survives = refuted < (voters / 2)
    return {
        "claim": claim,
        "voters": voters,
        "refuted": refuted,
        "survives": survives,
        "notes": notes,
    }
