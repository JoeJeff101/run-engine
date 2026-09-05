"""Governed artifacts: the brief, the contract, the spec, and the amendment path.

Nothing else in the engine writes these. An agent that can edit the rules it is
judged against is not being judged.
"""

from __future__ import annotations

from .amend import propose, ratify, vote
from .model import (
    APPROVED, PENDING, RATIFIED, REJECTED,
    Amendment, Brief, ChangelogEntry, Contract, GateSpec, MasterMetric,
    RequirementSet, Spec, SpecError,
)

__all__ = [
    "APPROVED", "PENDING", "RATIFIED", "REJECTED",
    "Amendment", "Brief", "ChangelogEntry", "Contract", "GateSpec", "MasterMetric",
    "RequirementSet", "Spec", "SpecError", "propose", "ratify", "vote",
]
