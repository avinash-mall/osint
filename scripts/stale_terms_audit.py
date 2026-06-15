#!/usr/bin/env python3
"""Audit Sentinel docs for removed-model terms presented as current capability.

Removed inference layers — Prithvi, Grounding-DINO, LAE-DINO, RemoteCLIP,
SAM3 AMG, and the FAIR1M-OBB detector — keep resurfacing in prose as if they
were still shipped (see the stale-term warning in ``docs/agent-entry.md``).
``scripts/docs_audit.py`` validates index/link/route structure but does **not**
catch this class of drift.

This linter is deliberately low-noise: a removed term is only flagged when its
line lacks removal/historical language. So a sentence like "the RemoteCLIP
verifier was removed" or a "~~Prithvi~~ ❌ Removed" benchmark row stays clean,
while a stale "current stack: SAM3 · Prithvi · Grounding DINO" line fails.

Read-only. Exits non-zero on any unexplained hit.

Scope:
    README.md and docs/**/*.md.

Allowlisted (removed terms are expected there):
    docs/decisions/, docs/archive/, docs/benchmarks/ (dated point-in-time
    snapshots that legitimately name whatever ran at that date), and
    docs/agent-entry.md (the canonical stale-term brief that intentionally
    names every removed model).

Per-line escape hatch:
    Add the marker ``stale-term-ok`` anywhere on the offending line (e.g. in a
    trailing ``<!-- stale-term-ok: ... -->`` comment) to suppress a deliberate
    historical reference the context heuristic misses.

Usage:
    python scripts/stale_terms_audit.py            # audit, exit 1 on drift
    python scripts/stale_terms_audit.py --list     # list every hit, still exits 1
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (label, pattern). Case-insensitive, word-bounded. NOTE: FAIR1M is removed only
# as a *detector* (FAIR1M-OBB); the FAIR1M reference dataset is intentionally
# kept for reference-platform embeddings, so the bare term is never matched.
REMOVED = [
    ("Prithvi", re.compile(r"\bPrithvi\b", re.I)),
    ("Grounding-DINO", re.compile(r"\bGrounding[ -]?DINO\b", re.I)),
    ("LAE-DINO", re.compile(r"\bLAE[ -]?DINO\b", re.I)),
    ("RemoteCLIP", re.compile(r"\bRemoteCLIP\b", re.I)),
    ("SAM3 AMG", re.compile(r"\bSAM ?3[ -]?AMG\b|\bAutomatic Mask Generator\b", re.I)),
    ("FAIR1M-OBB", re.compile(r"\bFAIR1M[ -]?OBB\b|\bfair1m_obb\b", re.I)),
]

# A line that names a removed term *and* carries removal/historical language is
# documenting the removal, not advertising the capability — leave it alone.
CONTEXT_OK = re.compile(
    r"remov|delet|\bdrop|no longer|replac|absent|deprecat|reject|"
    r"no active|since been|used to|formerly|former |previously|~~|stale-term-ok",
    re.I,
)

ALLOW_PREFIXES = ("docs/decisions/", "docs/archive/", "docs/benchmarks/")
ALLOW_FILES = ("docs/agent-entry.md",)


def targets() -> list[Path]:
    files: list[Path] = []
    readme = ROOT / "README.md"
    if readme.exists():
        files.append(readme)
    files.extend(sorted((ROOT / "docs").rglob("*.md")))
    return files


def is_allowed(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    return rel in ALLOW_FILES or rel.startswith(ALLOW_PREFIXES)


def audit() -> list[tuple[str, int, str, str]]:
    hits: list[tuple[str, int, str, str]] = []
    for path in targets():
        if is_allowed(path):
            continue
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CONTEXT_OK.search(line):
                continue
            for label, pattern in REMOVED:
                if pattern.search(line):
                    hits.append((rel, lineno, label, line.strip()))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", action="store_true", help="print every hit with its source line"
    )
    args = parser.parse_args()

    hits = audit()
    if not hits:
        print("stale-term audit passed: no removed-model drift")
        return 0

    for rel, lineno, label, line in hits:
        print(f"error: {rel}:{lineno}: removed model '{label}' presented as current")
        if args.list:
            print(f"    {line}")
    print(
        f"\nstale-term audit failed: {len(hits)} hit(s). "
        "Update the doc, or add 'stale-term-ok' to the line if the mention is "
        "an intentional historical/removal reference.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
