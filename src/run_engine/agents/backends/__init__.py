"""Concrete model backends.

The engine talks to the ``LLMBackend`` protocol and never to a vendor. Adding a
provider is a file here plus an entry in ``BACKENDS``; no topology, seat or gate
changes. That indirection only earns its keep if at least one real backend
exists, which is what this package is for -- an interface with no
implementation is a diagram.
"""

from __future__ import annotations

from typing import Callable

from .anthropic import AnthropicBackend

BACKENDS: dict[str, Callable[..., object]] = {
    "anthropic": AnthropicBackend,
}

__all__ = ["BACKENDS", "AnthropicBackend"]
