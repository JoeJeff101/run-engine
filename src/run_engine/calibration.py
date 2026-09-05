"""Calibration: the part almost nobody does.

Log every gate prediction *before* the gate runs, then score it afterwards. If
your 70% predictions come true 40% of the time, the engine has caught you being
optimistic, and the correction is structural rather than motivational -- file
an amendment that discounts the priors, ratify it, bump the version.

The ordering is the whole point and it is enforced by where the write happens:
predictions are appended during the run, before any gate result is recorded, so
a prediction cannot be quietly written after the outcome is known. Calibration
is only available in hindsight if you wrote it down in foresight.

After five or six runs the headline number stops being a story and starts being
a measurement, which is the only sense in which "most probable percent of
success" means anything at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

PREDICTION_LOG = "_prediction_log.jsonl"


@dataclass(frozen=True)
class Prediction:
    """A forecast, written down before the thing it forecasts happens."""

    run_id: str
    term: str
    predicted: float
    at: str = ""
    outcome: int | None = None  # 1 = the gate passed, 0 = it failed, None = unresolved

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "term": self.term, "predicted": self.predicted,
                "at": self.at, "outcome": self.outcome}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Prediction":
        outcome = raw.get("outcome")
        return cls(
            run_id=str(raw.get("run_id", "")), term=str(raw.get("term", "")),
            predicted=float(raw.get("predicted", 0.0)), at=str(raw.get("at", "")),
            outcome=None if outcome is None else int(outcome),
        )

    @property
    def resolved(self) -> bool:
        return self.outcome is not None


def record_predictions(
    folder,
    terms: dict[str, float],
    *,
    run_id: str,
    at: str | None = None,
) -> list[Prediction]:
    """Append this run's forecasts to its prediction log.

    Called before any gate is evaluated. ``folder`` is a RunFolder; the write
    goes through its seal check, so a sealed run cannot acquire predictions
    after the fact.
    """
    stamp = at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    written: list[Prediction] = []
    for term, predicted in terms.items():
        prediction = Prediction(run_id=run_id, term=term, predicted=float(predicted), at=stamp)
        folder.append_jsonl(PREDICTION_LOG, prediction.to_dict())
        written.append(prediction)
    return written


def resolve(predictions: Iterable[Prediction], results: dict[str, str]) -> list[Prediction]:
    """Attach known gate results to earlier predictions.

    Only pass/fail resolves a prediction. A pending or defunded gate leaves the
    forecast open -- scoring it either way would be inventing an outcome.
    """
    out: list[Prediction] = []
    for p in predictions:
        status = str(results.get(p.term, "")).lower()
        if p.resolved or status not in ("pass", "fail"):
            out.append(p)
            continue
        out.append(Prediction(p.run_id, p.term, p.predicted, p.at, 1 if status == "pass" else 0))
    return out


def brier(predictions: Sequence[Prediction]) -> float | None:
    """Mean squared error of resolved forecasts. Lower is better; 0.25 is a coin flip.

    Returns None when nothing has resolved yet, rather than 0.0 -- a perfect
    score from an empty history is the most flattering possible lie.
    """
    resolved = [p for p in predictions if p.resolved]
    if not resolved:
        return None
    return sum((p.predicted - float(p.outcome)) ** 2 for p in resolved) / len(resolved)


def reliability(predictions: Sequence[Prediction], *, bins: int = 5) -> list[tuple[str, int, float, float]]:
    """Predicted-vs-actual by confidence band: (label, n, mean predicted, observed rate)."""
    resolved = [p for p in predictions if p.resolved]
    out: list[tuple[str, int, float, float]] = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        group = [p for p in resolved if lo <= p.predicted < hi or (i == bins - 1 and p.predicted == 1.0)]
        if not group:
            continue
        out.append((
            f"{lo:.0%}–{hi:.0%}",
            len(group),
            sum(p.predicted for p in group) / len(group),
            sum(float(p.outcome) for p in group) / len(group),
        ))
    return out


def load_log(path: str | Path) -> list[Prediction]:
    """Read one prediction log. A malformed line is skipped, not fatal."""
    p = Path(path)
    if not p.is_file():
        return []
    out: list[Prediction] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Prediction.from_dict(json.loads(line)))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return out


def load_all(runs_dir: str | Path) -> list[Prediction]:
    """Every prediction across every archived run, oldest first."""
    root = Path(runs_dir)
    if not root.is_dir():
        return []
    out: list[Prediction] = []
    for entry in sorted(root.iterdir()):
        out.extend(load_log(entry / PREDICTION_LOG))
    return out


def report(predictions: Sequence[Prediction]) -> str:
    """The calibration section, as markdown."""
    score = brier(predictions)
    resolved = [p for p in predictions if p.resolved]
    lines = ["# Calibration", ""]

    if score is None:
        lines += [
            f"{len(predictions)} prediction(s) logged, none resolved yet. "
            "Brier score: not available.", "",
            "That is the expected state early on. The log exists so that the "
            "question 'is this engine's 70% actually a 70%?' becomes answerable "
            "later, and it only becomes answerable if the forecasts were written "
            "down before the results.",
        ]
        return "\n".join(lines) + "\n"

    lines += [f"**Brier score: {score:.4f}** over {len(resolved)} resolved prediction(s). "
              f"Lower is better; 0.25 is what you get by always saying 50%.", ""]

    bands = reliability(predictions)
    if bands:
        lines += ["| Confidence band | n | Mean predicted | Observed rate |",
                  "|---|---:|---:|---:|"]
        for label, n, predicted, observed in bands:
            lines.append(f"| {label} | {n} | {predicted:.0%} | {observed:.0%} |")
        lines.append("")

    drift = sum(p.predicted for p in resolved) / len(resolved) - \
        sum(float(p.outcome) for p in resolved) / len(resolved)
    if abs(drift) > 0.15:
        direction = "optimistic" if drift > 0 else "pessimistic"
        lines += [
            f"> **Systematically {direction}** by {abs(drift):.0%}. This is a spec problem, "
            f"not an attitude problem: file an amendment adjusting the gate priors, have it "
            f"ratified, and bump the version. The correction belongs in the constitution "
            f"where the next run will inherit it.", "",
        ]
    return "\n".join(lines) + "\n"
