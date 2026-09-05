#!/usr/bin/env python3
"""Pre-publication leak scanner.

Checks a working tree (or piped text, e.g. ``git log -p``) for material that
should never reach a public repository: credentials, contact addresses,
absolute developer paths, and -- optionally -- a private list of project terms.

Why the private list lives outside the repo
-------------------------------------------
The obvious design is to ship a denylist file enumerating the terms you are
protecting. That design is self-defeating: the file *is* the disclosure. Anyone
reading it learns exactly what you did not want them to know, and it is the
first thing a curious visitor greps for.

So this script ships only *generic* patterns. Project-specific terms are read
from a file outside the repository, named by ``$LEAK_DENYLIST`` or found at
``~/.config/evidence-pipeline/denylist.txt``. The scanner is public; the list
is not.

Usage
-----
    python scripts/scan_for_leaks.py .              # scan a tree
    git log -p | python scripts/scan_for_leaks.py - # scan history

Exit code 0 means clean, 1 means findings. Intended for a pre-push hook or CI.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# --- Generic patterns. Safe to publish: these describe shapes, not secrets. ---
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Credential shapes. Kept as fragments so this file does not itself trip
    # a naive secret scanner that greps for the literal prefixes.
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}")),
    ("google-key", re.compile(r"\bAIza[A-Za-z0-9\-_]{30,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[abps]-[0-9A-Za-z\-]{10,}")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer-literal", re.compile(r"[Bb]earer\s+[A-Za-z0-9\-._~+/]{20,}")),
    # Assignment of a secret-ish name to a non-empty literal.
    (
        "inline-secret-assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*"
            r"['\"][^'\"\s${}]{8,}['\"]"
        ),
    ),
    # Personally identifying / machine-identifying material.
    ("email-address", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")),
    ("absolute-home-path", re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+/")),
]

# Emails that are legitimately part of a public repo (docs, placeholders).
EMAIL_ALLOW = re.compile(
    r"(?i)@(example\.(com|org|net)|users\.noreply\.github\.com)\b"
    r"|\byour[._-]?email@|\bcontact@example\b"
)

SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".cache", "_router_cache",
    "node_modules", ".venv", "venv", "build", "dist", ".mypy_cache",
}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".docx", ".xlsx",
    ".npy", ".pyc", ".so", ".dylib", ".woff", ".woff2", ".ico",
}
MAX_BYTES = 4_000_000


def load_denylist(explicit: str | None) -> tuple[list[str], str | None]:
    """Return (terms, source_path). Missing list is a warning, not an error."""
    candidates = [
        explicit,
        os.environ.get("LEAK_DENYLIST"),
        str(Path.home() / ".config" / "evidence-pipeline" / "denylist.txt"),
    ]
    for cand in candidates:
        if not cand:
            continue
        path = Path(cand).expanduser()
        if path.is_file():
            terms = [
                line.strip().lower()
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            return terms, str(path)
    return [], None


def iter_files(root: Path):
    if root.is_file():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        yield path


def scan_text(text: str, terms: list[str], label: str) -> list[tuple[str, int, str, str]]:
    """Return findings as (label, lineno, kind, evidence)."""
    findings: list[tuple[str, int, str, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(line) > 2000:
            line = line[:2000]
        for kind, pattern in PATTERNS:
            for match in pattern.finditer(line):
                hit = match.group(0)
                if kind == "email-address" and EMAIL_ALLOW.search(hit):
                    continue
                findings.append((label, lineno, kind, redact(hit)))
        low = line.lower()
        for term in terms:
            if term in low:
                # Report the category, never the term itself -- output of this
                # scanner may be pasted into a log, an issue, or a CI console.
                findings.append((label, lineno, "denylist-term", "<redacted>"))
                break
    return findings


def redact(hit: str) -> str:
    if len(hit) <= 12:
        return hit
    return f"{hit[:6]}...{hit[-4:]} ({len(hit)} chars)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", nargs="?", default=".", help="directory, file, or '-' for stdin")
    ap.add_argument("--denylist", help="path to a private denylist file")
    ap.add_argument("--quiet", action="store_true", help="only print findings")
    args = ap.parse_args()

    terms, source = load_denylist(args.denylist)

    if not args.quiet:
        if source:
            print(f"denylist: {len(terms)} terms from {source}")
        else:
            print("denylist: none found (generic patterns only)", file=sys.stderr)

    findings: list[tuple[str, int, str, str]] = []
    scanned = 0

    if args.target == "-":
        findings += scan_text(sys.stdin.read(), terms, "<stdin>")
        scanned = 1
    else:
        root = Path(args.target).expanduser().resolve()
        for path in iter_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scanned += 1
            rel = path.relative_to(root) if path != root else path.name
            findings += scan_text(text, terms, str(rel))

    if not args.quiet:
        print(f"scanned: {scanned} file(s)")

    if findings:
        print(f"\nFAIL: {len(findings)} finding(s)\n")
        for label, lineno, kind, evidence in findings:
            print(f"  {label}:{lineno}: {kind}: {evidence}")
        print("\nResolve every finding before publishing.")
        return 1

    print("\nPASS: no findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
