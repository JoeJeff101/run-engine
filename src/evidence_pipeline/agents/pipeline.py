"""Topology 3: thread-per-role with queue handoff and a sole writer.

Scout -> research workers -> verifier -> output. One thread per role, connected
by queues, with a shared stop event.

The design constraint that shapes everything here is the **sole writer**. Only
the verifier thread writes output. Research workers hand drafts across a queue
and never touch the destination.

Two things fall out of that, and both matter more than they sound:

*There are no write races.* Not "races are handled" -- there is exactly one
writer, so the class of bug does not exist. Concurrency bugs in a system whose
output is a knowledge base are especially nasty because they do not crash; they
produce a file that is subtly wrong and looks fine.

*Verification cannot be skipped.* If workers could write, verification becomes a
step that a sufficiently confident agent can route around. Making the verifier
the only path to disk turns "please check your work" from a request into a
property of the topology. A draft whose citations do not trace back to the
records that draft actually retrieved is quarantined rather than written --
kept for inspection, but never allowed into the output.

Bounded queues do the backpressure: if the verifier falls behind, workers block
rather than accumulating an unbounded backlog of unverified drafts.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .backend import LLMBackend

SENTINEL = object()


@dataclass
class Draft:
    topic: str
    body: str
    citations: list[str] = field(default_factory=list)
    worker: int = 0


@dataclass
class PipelineResult:
    written: list[Draft] = field(default_factory=list)
    quarantined: list[tuple[Draft, str]] = field(default_factory=list)
    topics_scouted: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"scouted:     {self.topics_scouted}",
            f"written:     {len(self.written)}",
            f"quarantined: {len(self.quarantined)}",
        ]
        for draft, why in self.quarantined:
            lines.append(f"  ! {draft.topic}: {why}")
        for err in self.errors:
            lines.append(f"  ERROR {err}")
        return "\n".join(lines)


def _extract_citations(text: str) -> list[str]:
    from ..ledger import PRIMARY_SOURCE_RE

    return [m.group(0) for m in PRIMARY_SOURCE_RE.finditer(text or "")]


def run_pipeline(
    topics: list[str],
    backend: LLMBackend,
    router: Any | None = None,
    workers: int = 3,
    budget: str = "lean",
    writer: Callable[[Draft], None] | None = None,
    queue_size: int = 8,
) -> PipelineResult:
    """Run scout -> workers -> verifier. Returns once every topic is resolved."""
    result = PipelineResult()
    topic_q: queue.Queue = queue.Queue(maxsize=queue_size)
    draft_q: queue.Queue = queue.Queue(maxsize=queue_size)
    stop = threading.Event()
    lock = threading.Lock()

    # -- scout ------------------------------------------------------------
    def scout() -> None:
        try:
            for topic in topics:
                if stop.is_set():
                    break
                topic_q.put(topic)
                with lock:
                    result.topics_scouted += 1
        except Exception as exc:  # pragma: no cover - defensive
            with lock:
                result.errors.append(f"scout: {exc}")
        finally:
            for _ in range(workers):
                topic_q.put(SENTINEL)

    # -- research workers -------------------------------------------------
    def worker(index: int) -> None:
        while not stop.is_set():
            item = topic_q.get()
            if item is SENTINEL:
                topic_q.task_done()
                break
            topic = str(item)
            try:
                retrieved = ""
                citations: list[str] = []
                if router is not None:
                    found = router.search(question=topic, budget=budget)
                    retrieved = found.pack(pack_chars=4000, excerpt_chars=300)
                    for rec in found.records:
                        if rec.entity_id:
                            citations.append(str(rec.entity_id))
                        if rec.doi:
                            citations.append(rec.doi)

                body = backend.complete(
                    system=(
                        "You are a research scribe. Summarize what the retrieved "
                        "records establish about the topic. Cite by identifier. "
                        "If the records do not address the topic, say so."
                    ),
                    prompt=f"Topic: {topic}\n\n{retrieved}\n\nWrite the summary.",
                    tier="research",
                    temperature=0.4,
                    max_tokens=1024,
                )
                draft_q.put(Draft(topic=topic, body=body, citations=citations, worker=index))
            except Exception as exc:
                with lock:
                    result.errors.append(f"worker {index} on {topic!r}: {exc}")
            finally:
                topic_q.task_done()

    # -- verifier: the ONLY writer ---------------------------------------
    def verifier() -> None:
        finished = 0
        while finished < workers:
            item = draft_q.get()
            if item is SENTINEL:
                finished += 1
                draft_q.task_done()
                continue
            draft: Draft = item  # type: ignore[assignment]
            try:
                claimed = _extract_citations(draft.body)
                # Every identifier the draft cites must appear among the records
                # that draft actually retrieved. A citation the worker did not
                # retrieve was invented, however plausible it looks.
                untraceable = [c for c in claimed if c not in draft.citations]
                if untraceable:
                    with lock:
                        result.quarantined.append(
                            (draft, f"{len(untraceable)} citation(s) do not trace to retrieved records")
                        )
                elif not draft.body.strip():
                    with lock:
                        result.quarantined.append((draft, "empty draft"))
                else:
                    if writer:
                        writer(draft)
                    with lock:
                        result.written.append(draft)
            except Exception as exc:
                with lock:
                    result.errors.append(f"verifier on {draft.topic!r}: {exc}")
            finally:
                draft_q.task_done()

    scout_thread = threading.Thread(target=scout, name="scout", daemon=True)
    worker_threads = [
        threading.Thread(target=worker, args=(i + 1,), name=f"worker-{i + 1}", daemon=True)
        for i in range(workers)
    ]
    verifier_thread = threading.Thread(target=verifier, name="verifier", daemon=True)

    verifier_thread.start()
    for thread in worker_threads:
        thread.start()
    scout_thread.start()

    scout_thread.join()
    for thread in worker_threads:
        thread.join()
    # One sentinel per worker so the verifier knows every producer is finished.
    for _ in range(workers):
        draft_q.put(SENTINEL)
    verifier_thread.join()

    return result
