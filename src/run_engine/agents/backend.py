"""Model backends, tiering, and cost accounting.

The backend is an interface with a deterministic offline implementation, which
is what makes the orchestration in this package testable and runnable by anyone
who clones the repo with no credentials at all. Every topology here executes end
to end against ``OfflineBackend``.

That is not just a convenience for demos. Orchestration bugs -- a seat that
never receives its upstream context, a judge that silently sees nothing, a
worker pool that double-claims -- are structural, and they reproduce perfectly
against a deterministic stub. Debugging them against a live model means paying
per iteration to watch nondeterministic output, which is slower *and* worse.

Tiering
-------
Assigning every seat the most capable model is the single easiest way to make a
multi-agent system indefensibly expensive. A seventeen-seat board run entirely
on a frontier model costs seventeen times what it needs to, and most seats are
doing work a cheaper model does indistinguishably well. Seats declare a tier;
tiers map to models; the map lives in configuration.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# Tier -> environment variable holding the concrete model id. Deliberately
# indirect: the topology should never name a vendor model, so swapping
# providers is a config change and not a code change.
TIER_ENV = {
    "heavy": "MODEL_HEAVY",
    "light": "MODEL_LIGHT",
    "research": "MODEL_RESEARCH",
    # The outside view. Deliberately a *different vendor* from the others, and
    # the only tier whose value is chosen for who trained it rather than for how
    # capable it is. See OUTSIDE below.
    "outside": "MODEL_OUTSIDE",
}

TIER_FALLBACK = {
    "heavy": "tier:heavy", "light": "tier:light", "research": "tier:research",
    "outside": "tier:outside",
}

# Why a tier exists for provenance rather than capability
# ------------------------------------------------------
# Two seats running the same model do not disagree independently. They share a
# training distribution, so they share the things that distribution got wrong,
# and an adversarial pairing between them checks style rather than substance --
# the attacker cannot see the blind spot it was trained into, because the target
# was trained into the same one.
#
# Routing an attacker to a model from a different vendor does not make it
# smarter. It makes its errors *uncorrelated with its target's*, which is the
# only property that matters when the seat's job is to find what another seat
# missed. Cross-provider disagreement is therefore a signal in its own right:
# when the outside seat objects, the objection is more likely to be about the
# claim than about a shared habit of thought.
OUTSIDE = "outside"

# Indicative USD per million tokens (input, output). Real numbers vary by
# provider and change often; override via ``PRICES`` for accurate accounting.
# The point of having them at all is that an estimate printed at the end of
# every run makes cost a visible design constraint rather than a monthly
# surprise.
PRICES: dict[str, tuple[float, float]] = {
    "heavy": (5.00, 25.00),
    "light": (3.00, 15.00),
    "research": (1.25, 10.00),
    "outside": (5.00, 15.00),
}


def model_for(tier: str) -> str:
    return os.environ.get(TIER_ENV.get(tier, ""), "") or TIER_FALLBACK.get(tier, "tier:light")


def tier_is_configured(tier: str) -> bool:
    """Whether this tier resolves to a real model id rather than a placeholder."""
    return bool(os.environ.get(TIER_ENV.get(tier, ""), ""))


def resolve_tier(tier: str, *, diverse: bool = False, attacks: bool = False) -> str:
    """The tier a seat actually runs at.

    In diverse mode a seat that is chartered to attack another is routed to the
    outside tier, because an attacker sharing its target's training distribution
    shares its blind spots. If no outside model is configured the seat keeps its
    declared tier -- degrading loudly beats pretending to an independence the
    run does not have, and ``diversity_note`` says so in the archive.
    """
    if diverse and attacks and tier_is_configured(OUTSIDE):
        return OUTSIDE
    return tier


def diversity_note(diverse: bool) -> str:
    """One line for the run record about how independent the attacks really were."""
    if not diverse:
        return ("Attacks ran on the same provider as their targets: disagreement here "
                "is evidence about the argument, not about the model.")
    if tier_is_configured(OUTSIDE):
        return (f"Diverse routing ON: attacking seats ran on {model_for(OUTSIDE)}, a "
                f"different provider from their targets.")
    return ("Diverse routing REQUESTED but MODEL_OUTSIDE is unset, so attacking seats "
            "ran on their declared tier. The attacks are not provider-independent.")


@runtime_checkable
class LLMBackend(Protocol):
    """Minimal surface every backend must provide."""

    def complete(
        self,
        system: str,
        prompt: str,
        tier: str = "light",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str: ...


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_tier: dict[str, int] = field(default_factory=dict)

    def add(self, tier: str, in_tokens: int, out_tokens: int) -> None:
        self.calls += 1
        self.input_tokens += in_tokens
        self.output_tokens += out_tokens
        self.by_tier[tier] = self.by_tier.get(tier, 0) + 1

    def estimated_cost(self) -> float:
        """Rough USD. Attributes tokens per tier by call share."""
        if not self.calls:
            return 0.0
        total = 0.0
        for tier, calls in self.by_tier.items():
            share = calls / self.calls
            rate_in, rate_out = PRICES.get(tier, PRICES["light"])
            total += (self.input_tokens * share / 1e6) * rate_in
            total += (self.output_tokens * share / 1e6) * rate_out
        return round(total, 4)

    def report(self) -> str:
        tiers = ", ".join(f"{t}:{n}" for t, n in sorted(self.by_tier.items())) or "none"
        return (
            f"{self.calls} calls ({tiers}) | "
            f"~{self.input_tokens:,} in / ~{self.output_tokens:,} out tokens | "
            f"~${self.estimated_cost():.2f}"
        )


class CallCapExceeded(Exception):
    """Raised when a run exhausts its hard call budget."""


class BaseBackend:
    """Shared bookkeeping: usage tracking and a hard per-run call cap.

    The cap is a real ceiling, not a warning. An agent loop that has gone wrong
    -- retrying a malformed tool call, or two seats arguing in circles -- will
    otherwise spend until someone notices. Past the cap, calls refuse.
    """

    def __init__(self, call_cap: int | None = None) -> None:
        self.usage = Usage()
        self.call_cap = call_cap

    def _check_cap(self) -> None:
        if self.call_cap is not None and self.usage.calls >= self.call_cap:
            raise CallCapExceeded(f"run exhausted its cap of {self.call_cap} model calls")

    @staticmethod
    def _tokens(text: str) -> int:
        # ~4 characters per token. Good enough for budgeting; nobody should be
        # making decisions on the third significant figure of an estimate.
        return max(1, len(text) // 4)


class OfflineBackend(BaseBackend):
    """Deterministic stub. No network, no keys, no nondeterminism.

    Responses are derived from a hash of the inputs, so the same board produces
    the same transcript every time -- which is what makes the topologies
    unit-testable. Where a caller declares an output contract via
    ``RESPOND_WITH:``, the stub honours it, so downstream parsers are exercised
    for real rather than bypassed.
    """

    def __init__(self, call_cap: int | None = None, seed: str = "offline") -> None:
        super().__init__(call_cap=call_cap)
        self.seed = seed
        self.transcript: list[dict[str, str]] = []

    def _digest(self, *parts: str) -> str:
        joined = " ".join([self.seed, *parts])
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def complete(
        self,
        system: str,
        prompt: str,
        tier: str = "light",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        self._check_cap()
        digest = self._digest(system, prompt, tier, f"{temperature:.2f}")
        text = self._render(prompt, digest, temperature)
        self.usage.add(tier, self._tokens(system + prompt), self._tokens(text))
        self.transcript.append({"tier": tier, "prompt": prompt[:200], "response": text[:200]})
        return text

    def _render(self, prompt: str, digest: str, temperature: float) -> str:
        contract = ""
        for line in prompt.splitlines():
            if line.strip().upper().startswith("RESPOND_WITH:"):
                contract = line.split(":", 1)[1].strip().lower()
                break

        short = digest[:8]
        if contract == "ledger":
            # The tournament judge's output contract.
            return (
                "<<LEDGER>>\n"
                f"option-{digest[0:2]} | INCUMBENT | strongest support in retrieved sources\n"
                f"option-{digest[2:4]} | FALLBACK  | viable but weaker evidence base\n"
                f"option-{digest[4:6]} | DEAD-END  | contradicted by retrieved sources\n"
                "<<END>>"
            )
        if contract == "verdict":
            # The verifier's output contract. Refuses more often at low
            # temperature, mirroring how a careful reviewer actually behaves.
            supported = int(digest[:2], 16) % 3 != 0
            return (
                "<<VERDICT>>\n"
                f"support: {'SUPPORTED' if supported else 'UNSUPPORTED'}\n"
                f"traceable_citations: {'yes' if supported else 'no'}\n"
                f"note: offline determination {short}\n"
                "<<END>>"
            )
        if contract == "scores":
            # The judge's scorecard contract. Deterministic but varied, so the
            # ranking logic is exercised with a real spread rather than a tie.
            def mark(offset: int, ceiling: int = 5) -> int:
                return int(digest[offset : offset + 2], 16) % (ceiling + 1)

            rows = []
            for i in range(3):
                base = i * 6
                pen = "logical_leap" if mark(base + 8) > 3 else ""
                rows.append(
                    f"option-{digest[base:base+2]} | "
                    f"evidence_density={mark(base)} specificity={mark(base + 2)} "
                    f"falsifiability={mark(base + 4)} coverage_honesty={mark(base + 6)} | "
                    f"{pen} | offline determination {short}"
                )
            return "<<SCORES>>\n" + "\n".join(rows) + "\n<<END>>"
        if contract == "topics":
            return "\n".join(f"- candidate question {short}-{i}" for i in range(1, 4))

        return (
            f"[offline:{short}] Findings under temperature {temperature:.2f}.\n"
            "- Observation grounded in the supplied context.\n"
            "- One open question that the retrieved sources do not settle.\n"
            "- Abstained where the context was insufficient."
        )


def default_backend() -> LLMBackend:
    """Offline unless a real backend is explicitly configured.

    Defaulting to offline is a safety property: a fresh clone cannot
    accidentally spend money, and CI cannot accidentally reach a provider.
    """
    return OfflineBackend()
