"""A real backend, so ``--live`` is not a dead end.

The offline stub is what makes this repository runnable by anyone with no
credentials, and it is genuinely the right default -- orchestration bugs
reproduce perfectly against a deterministic stub, and debugging them against a
live model means paying per iteration to watch nondeterministic output.

But a decision engine that has never actually thought is a diagram of a
decision engine. This module is the other half: given a key, the same boards,
gates and ledger run against a real model.

Three things are deliberate.

**The model ids come from the environment**, via the tier map in
``agents.backend``. A seat declares a tier; the tier resolves to whatever
``MODEL_HEAVY`` / ``MODEL_LIGHT`` / ``MODEL_RESEARCH`` name, so a topology never
mentions a vendor model and swapping providers stays a config change.

**Failure is loud and specific.** A missing key names the variable; a missing
SDK names the install command. The failure mode this avoids is a live run that
silently produces stub output and gets filed as evidence.

**The SDK is imported lazily**, inside the constructor, so the package imports
cleanly and the whole test suite runs with no vendor dependency installed.
"""

from __future__ import annotations

import os
from typing import Any

from ..backend import BaseBackend, model_for

DEFAULT_MODELS = {
    # Only used when the tier variables are unset. Deliberately conservative:
    # a run that silently picks the most expensive model is a bill nobody chose.
    "heavy": "claude-sonnet-4-5",
    "light": "claude-haiku-4-5",
    "research": "claude-sonnet-4-5",
}

KEY_ENV = "ANTHROPIC_API_KEY"


class MissingCredential(RuntimeError):
    """Raised when a live run is requested without the credential it needs."""


class AnthropicBackend(BaseBackend):
    """``LLMBackend`` against the Anthropic Messages API."""

    def __init__(self, *, api_key: str | None = None, call_cap: int | None = 200,
                 client: Any = None, timeout: float = 120.0) -> None:
        super().__init__(call_cap=call_cap)
        self.timeout = timeout

        if client is not None:  # injected in tests; never touches the network
            self.client = client
            return

        key = api_key or os.environ.get(KEY_ENV, "")
        if not key:
            raise MissingCredential(
                f"a live run needs {KEY_ENV}, which is not set in this environment.\n"
                f"  export {KEY_ENV}=sk-ant-...\n"
                f"Or drop --live to run offline against the deterministic stub, which "
                f"needs no credentials and exercises every topology end to end."
            )
        try:
            import anthropic  # noqa: PLC0415  (lazy on purpose: see module docstring)
        except ImportError as exc:
            raise MissingCredential(
                "a live run needs the Anthropic SDK:\n"
                "  pip install 'run-engine[live]'   (or: pip install anthropic)"
            ) from exc
        self.client = anthropic.Anthropic(api_key=key, timeout=timeout)

    # -- the protocol -------------------------------------------------------

    def model_for_tier(self, tier: str) -> str:
        resolved = model_for(tier)
        # model_for falls back to a "tier:x" placeholder when the env var is
        # unset. That placeholder is fine for the offline stub and meaningless
        # to a real API, so substitute a concrete default here.
        return DEFAULT_MODELS.get(tier, DEFAULT_MODELS["light"]) \
            if resolved.startswith("tier:") else resolved

    def complete(self, system: str, prompt: str, tier: str = "light",
                 temperature: float = 0.7, max_tokens: int = 2048) -> str:
        self._check_cap()
        model = self.model_for_tier(tier)

        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )

        text = _text_of(response)
        usage = getattr(response, "usage", None)
        self.usage.add(
            tier,
            int(getattr(usage, "input_tokens", 0) or self._tokens(system + prompt)),
            int(getattr(usage, "output_tokens", 0) or self._tokens(text)),
        )
        return text


def _text_of(response: Any) -> str:
    """Concatenate the text blocks of a Messages response.

    Tolerant of shape rather than strict: a response carrying tool-use or
    thinking blocks alongside text should yield its text, not raise.
    """
    blocks = getattr(response, "content", None)
    if blocks is None:
        return str(response)
    parts: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def build(**kwargs: Any) -> AnthropicBackend:
    return AnthropicBackend(**kwargs)
