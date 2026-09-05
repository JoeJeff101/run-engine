"""The amendment path: the only way the constitution changes.

Two keys, two people. The board can vote to change the plan; an independent
ratifier still confirms the change is internally consistent and evidenced
before it lands. Then the version increments and a diff is written.

The asymmetry is deliberate. Proposing costs nothing and anyone may do it;
changing the spec costs two independent acts. Systems that let a single
approval mutate the rules drift without anyone being able to say when.

Why a diff and not an edit
--------------------------
History is preserved rather than overwritten so that "why did we raise the
price in June, and who agreed?" has an answer a year later. An edited document
cannot answer that question, and the absence is discovered exactly when it
matters most -- in diligence.
"""

from __future__ import annotations

import difflib
from datetime import datetime, timezone
from typing import Any

import yaml

from .model import APPROVED, PENDING, RATIFIED, REJECTED, Amendment, ChangelogEntry, Spec, SpecError

# Dotted paths an amendment is allowed to target. Anything else is refused,
# because an amendment that can write an arbitrary key can invent one.
SETTABLE_PREFIXES = ("gates.", "master_metric.", "market_prior.", "requirement_sets.")


def next_amendment_id(spec: Spec, prefix: str = "AMD") -> str:
    n = len(spec.amendments) + 1
    return f"{prefix}-{n:03d}"


def propose(spec: Spec, proposal: str, target: str, value: Any, by: str) -> Amendment:
    """Queue a change. Queuing is not changing."""
    if not proposal.strip():
        raise SpecError("amendment: proposal text is empty")
    if not by.strip():
        raise SpecError("amendment: every proposal is attributable -- 'by' is required")
    if not any(target.startswith(p) for p in SETTABLE_PREFIXES):
        raise SpecError(
            f"amendment: target {target!r} is not amendable. Amendable prefixes: "
            f"{', '.join(SETTABLE_PREFIXES)}"
        )
    _resolve(spec, target)  # fail now if the path does not exist, not at ratification
    amendment = Amendment(
        id=next_amendment_id(spec), proposal=proposal.strip(),
        target=target, value=value, proposed_by=by.strip(), status=PENDING,
    )
    spec.amendments.append(amendment)
    return amendment


def vote(spec: Spec, amendment_id: str, seat: str, ballot: str, *, threshold: float = 0.5) -> Amendment:
    """Record one seat's ballot. APPROVE / REJECT / AMEND.

    The first key. Crossing the threshold moves the amendment to ``approved``,
    which is *still not* a change to the spec.
    """
    amendment = _find(spec, amendment_id)
    if amendment.status == RATIFIED:
        raise SpecError(f"{amendment_id}: already ratified; propose a new amendment instead")

    ballot_u = ballot.strip().upper()
    if not ballot_u.startswith(("APPROVE", "REJECT", "AMEND")):
        raise SpecError(f"{amendment_id}: ballot must be APPROVE, REJECT or AMEND (got {ballot!r})")
    if seat == amendment.proposed_by:
        raise SpecError(
            f"{amendment_id}: {seat!r} proposed this amendment and cannot also vote on it. "
            f"The person who wants the change does not get to approve it."
        )
    amendment.votes[seat] = ballot_u

    approve, reject = amendment.tally()
    cast = approve + reject
    if cast:
        amendment.status = APPROVED if approve / cast > threshold else (
            REJECTED if reject / cast >= threshold else PENDING
        )
    return amendment


def ratify(spec: Spec, amendment_id: str, by: str, *, now: str | None = None) -> Spec:
    """The second key. Applies the change, writes the changelog, bumps the version.

    Refuses an amendment that has only been voted on, and refuses a ratifier
    who proposed it or voted on it -- otherwise the two keys are held by one
    pair of hands and the ceremony is theatre.
    """
    amendment = _find(spec, amendment_id)

    if amendment.status == RATIFIED:
        raise SpecError(f"{amendment_id}: already ratified")
    if amendment.status == REJECTED:
        raise SpecError(f"{amendment_id}: was rejected by the board and cannot be ratified")
    if amendment.status != APPROVED:
        raise SpecError(
            f"{amendment_id}: status is {amendment.status!r}. A vote alone cannot change the "
            f"spec -- an amendment must be approved by the board and then independently "
            f"ratified. Two keys, two people."
        )
    if not by.strip():
        raise SpecError("ratification is attributable -- 'by' is required")
    if by == amendment.proposed_by:
        raise SpecError(
            f"{amendment_id}: {by!r} proposed this amendment and cannot ratify it. "
            f"Two keys, two people."
        )
    if by in amendment.votes:
        raise SpecError(
            f"{amendment_id}: {by!r} voted on this amendment and cannot also ratify it. "
            f"Ratification is an independent check, not a second ballot."
        )

    before = yaml.safe_dump(spec.to_dict(), sort_keys=False, allow_unicode=True)
    _apply(spec, amendment.target, amendment.value)
    spec.version += 1
    amendment.status = RATIFIED
    amendment.ratified_by = by.strip()
    after = yaml.safe_dump(spec.to_dict(), sort_keys=False, allow_unicode=True)

    approve, _ = amendment.tally()
    spec.changelog.append(ChangelogEntry(
        version=spec.version,
        amendment_id=amendment.id,
        summary=amendment.proposal,
        diff=_diff(before, after, amendment.id),
        approved_by=", ".join(sorted(k for k, v in amendment.votes.items() if v.startswith("APPROVE"))),
        ratified_by=amendment.ratified_by,
        at=now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ))
    return spec


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _find(spec: Spec, amendment_id: str) -> Amendment:
    found = next((a for a in spec.amendments if a.id == amendment_id), None)
    if found is None:
        raise SpecError(f"no such amendment: {amendment_id}")
    return found


def _diff(before: str, after: str, label: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(), after.splitlines(),
        fromfile=f"spec (before {label})", tofile=f"spec (after {label})", lineterm="", n=2,
    )
    return "\n".join(lines)


def _split(target: str) -> list[str]:
    parts = [p for p in target.split(".") if p]
    if len(parts) < 2:
        raise SpecError(f"amendment target {target!r} is not specific enough")
    return parts


def _resolve(spec: Spec, target: str) -> Any:
    """Read the current value at a dotted path, raising if it does not exist."""
    head, *rest = _split(target)

    if head == "master_metric":
        if rest[0] not in ("performance", "do_no_harm"):
            raise SpecError(f"master_metric has no field {rest[0]!r}")
        return getattr(spec.master_metric, rest[0])

    if head == "market_prior":
        if rest[0] not in ("alpha", "beta"):
            raise SpecError("market_prior has only 'alpha' and 'beta'")
        return spec.market_prior[0 if rest[0] == "alpha" else 1]

    if head == "gates":
        gate = spec.gate(rest[0])
        if gate is None:
            raise SpecError(f"no such gate: {rest[0]}")
        if len(rest) < 2 or not hasattr(gate, rest[1]):
            raise SpecError(f"gate {rest[0]} has no field {rest[1] if len(rest) > 1 else ''!r}")
        return getattr(gate, rest[1])

    if head == "requirement_sets":
        rs = next((r for r in spec.requirement_sets if r.name == rest[0]), None)
        if rs is None:
            raise SpecError(f"no such requirement set: {rest[0]}")
        if len(rest) < 2 or not hasattr(rs, rest[1]):
            raise SpecError(f"requirement set {rest[0]} has no field {rest[1] if len(rest) > 1 else ''!r}")
        return getattr(rs, rest[1])

    raise SpecError(f"unamendable path: {target}")


def _apply(spec: Spec, target: str, value: Any) -> None:
    """Write a value at a dotted path. Frozen dataclasses are replaced, not mutated."""
    from dataclasses import replace

    head, *rest = _split(target)
    _resolve(spec, target)  # existence check first

    if head == "master_metric":
        spec.master_metric = replace(spec.master_metric, **{rest[0]: str(value)})
        return

    if head == "market_prior":
        a, b = spec.market_prior
        spec.market_prior = (float(value), b) if rest[0] == "alpha" else (a, float(value))
        return

    if head == "gates":
        gates = list(spec.gates)
        for i, g in enumerate(gates):
            if g.id == rest[0]:
                coerced = float(value) if rest[1] in ("cost", "prior_alpha", "prior_beta") else value
                gates[i] = replace(g, **{rest[1]: coerced})
        spec.gates = tuple(gates)
        return

    if head == "requirement_sets":
        sets = list(spec.requirement_sets)
        for i, rs in enumerate(sets):
            if rs.name == rest[0]:
                coerced = tuple(str(v) for v in value) if rest[1] == "requirements" else value
                sets[i] = replace(rs, **{rest[1]: coerced})
        spec.requirement_sets = tuple(sets)
        return
