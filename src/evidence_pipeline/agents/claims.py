"""Topology 4: worker pool over a file-locked claim ledger.

The problem this solves: several crews running as **separate OS processes**,
possibly started at different times from different terminals, working the same
list of topics. In-process locks are useless across processes, so coordination
has to live somewhere both processes can see -- a file.

Every claim is taken under ``fcntl.flock``, and every write is a temp-file write
followed by an atomic ``os.replace``. Together those give the property that
matters: two crews provably never do the same work, and a crash mid-write leaves
the previous ledger intact rather than a half-written file.

Stale reclamation exists because processes die. A claim held by a process that
was killed would otherwise block that topic forever, so a claim older than the
stale window is reclaimable by anyone. The window is a trade -- too short and
two crews duplicate slow work, too long and a crash costs you an hour of
throughput. Thirty minutes is calibrated to "longer than any single topic should
take, shorter than anyone's patience".

On Windows ``fcntl`` is unavailable; the lock degrades to a no-op and the module
still works single-process. That is stated rather than hidden, because silently
losing your mutual exclusion is exactly the kind of thing that produces a
corrupt ledger three hours into a run.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

try:
    import fcntl

    HAVE_FLOCK = True
except ImportError:  # pragma: no cover - platform dependent
    fcntl = None  # type: ignore[assignment]
    HAVE_FLOCK = False

STALE_SECONDS = 30 * 60
COLUMNS = ("claim_key", "crew", "status", "topic", "pid", "ts_claimed", "ts_done")


def _timestamp(now: float | None = None) -> str:
    """An exactly round-trippable timestamp.

    Any fixed-precision format rounds to *nearest*, which can place the stored
    time slightly in the future: at t=100.0006, `f"{t:.3f}"` records 100.001.
    Elapsed time then computes negative, and a claim whose age is negative can
    never become stale -- so a dead crew's claim blocks that topic forever.

    `repr` of a float round-trips exactly, so the stored value is the observed
    time rather than an approximation of it.
    """
    return repr(time.time() if now is None else now)


@dataclass
class Claim:
    claim_key: str
    crew: str
    status: str      # "claimed" | "done"
    topic: str
    pid: str
    ts_claimed: str
    ts_done: str = ""

    def to_row(self) -> str:
        return "\t".join(
            str(getattr(self, col)).replace("\t", " ").replace("\n", " ") for col in COLUMNS
        )

    @classmethod
    def from_row(cls, row: str) -> "Claim | None":
        cells = row.rstrip("\n").split("\t")
        if len(cells) < len(COLUMNS):
            return None
        return cls(**dict(zip(COLUMNS, cells)))

    def is_stale(self, now: float, window: int = STALE_SECONDS) -> bool:
        """Is this claim at least ``window`` seconds old?

        The comparison is ``>=`` rather than ``>`` so that ``window=0`` has a
        coherent meaning -- "reclaim anything" -- instead of depending on whether
        two operations landed in the same clock tick. Under a strict ``>``,
        elapsed time of exactly zero is not greater than zero, so a zero window
        reclaimed nothing sometimes and everything other times.

        An unparseable timestamp is treated as stale: a claim nobody can date is
        a claim nobody should be blocked by.
        """
        if self.status == "done":
            return False
        try:
            return (now - float(self.ts_claimed)) >= window
        except ValueError:
            return True


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Exclusive advisory lock on a sidecar file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    handle = open(lock_path, "a+")
    try:
        if HAVE_FLOCK:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if HAVE_FLOCK:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


class ClaimLedger:
    """Cross-process work allocation over a tab-separated file."""

    def __init__(self, path: str | Path, crew: str = "crew-a", stale_seconds: int = STALE_SECONDS):
        self.path = Path(path)
        self.crew = crew
        self.stale_seconds = stale_seconds

    def _read(self) -> list[Claim]:
        if not self.path.is_file():
            return []
        claims: list[Claim] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith(COLUMNS[0]):
                continue
            claim = Claim.from_row(line)
            if claim:
                claims.append(claim)
        return claims

    def _write(self, claims: list[Claim]) -> None:
        """Atomic replace. A reader never observes a partial ledger."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + f".tmp{os.getpid()}")
        body = ["\t".join(COLUMNS)] + [c.to_row() for c in claims]
        tmp.write_text("\n".join(body) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def claim(self, key: str, topic: str = "") -> bool:
        """Try to claim ``key``. True if this crew now owns it."""
        with _locked(self.path):
            claims = self._read()
            now = time.time()
            for existing in claims:
                if existing.claim_key != key:
                    continue
                if existing.status == "done":
                    return False
                if not existing.is_stale(now, self.stale_seconds):
                    return False
                # Stale: the previous holder is presumed dead. Take it over.
                existing.crew = self.crew
                existing.pid = str(os.getpid())
                existing.ts_claimed = _timestamp(now)
                self._write(claims)
                return True

            claims.append(
                Claim(
                    claim_key=key,
                    crew=self.crew,
                    status="claimed",
                    topic=topic or key,
                    pid=str(os.getpid()),
                    ts_claimed=_timestamp(now),
                )
            )
            self._write(claims)
            return True

    def complete(self, key: str) -> None:
        with _locked(self.path):
            claims = self._read()
            for claim in claims:
                if claim.claim_key == key and claim.crew == self.crew:
                    claim.status = "done"
                    claim.ts_done = _timestamp()
            self._write(claims)

    def summary(self) -> dict[str, int]:
        claims = self._read()
        return {
            "total": len(claims),
            "claimed": sum(1 for c in claims if c.status == "claimed"),
            "done": sum(1 for c in claims if c.status == "done"),
        }


def run_pool(
    keys: list[str],
    ledger: ClaimLedger,
    work: Callable[[str], None],
    concurrency: int = 3,
) -> dict[str, int]:
    """Process ``keys`` with a thread pool, skipping anything already claimed."""
    import queue as _queue
    import threading

    work_q: _queue.Queue = _queue.Queue()
    for key in keys:
        work_q.put(key)

    stats = {"done": 0, "skipped": 0, "failed": 0}
    lock = threading.Lock()

    def loop() -> None:
        while True:
            try:
                key = work_q.get_nowait()
            except _queue.Empty:
                return
            try:
                if not ledger.claim(key):
                    with lock:
                        stats["skipped"] += 1
                    continue
                work(key)
                ledger.complete(key)
                with lock:
                    stats["done"] += 1
            except Exception:
                # The claim is intentionally left open. It will age out and
                # become reclaimable, so a transient failure does not
                # permanently burn the topic.
                with lock:
                    stats["failed"] += 1
            finally:
                work_q.task_done()

    threads = [threading.Thread(target=loop, daemon=True) for _ in range(max(1, concurrency))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return stats
