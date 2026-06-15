#!/usr/bin/env python3
"""Run Sentinel's read-only repo-hygiene gates and report drift.

Bundles the two structural doc gates behind one entry point so the Claude Code
``Stop`` hook and the git ``pre-push`` hook share a single tested command:

    scripts/docs_audit.py         index/link/route/tag drift
    scripts/stale_terms_audit.py  removed-model terms shown as current capability

Modes:
    --check       run both, stream their output, exit 1 if either fails.
                  Used by the pre-push git hook as a hard gate.
    --stop-hook   run both, emit a Claude Code ``systemMessage`` JSON object on
                  drift (and always exit 0 so a turn is never trapped). Used by
                  the Stop hook to surface drift the moment a turn ends.

With no mode it behaves like --check.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GATES = (
    ("docs", ROOT / "scripts" / "docs_audit.py"),
    ("stale-terms", ROOT / "scripts" / "stale_terms_audit.py"),
)


def run_gates() -> tuple[bool, str]:
    """Return (ok, combined_output)."""
    ok = True
    chunks: list[str] = []
    for label, path in GATES:
        proc = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True,
            text=True,
        )
        out = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            ok = False
        chunks.append(f"[{label}] {out}" if out else f"[{label}] (no output)")
    return ok, "\n".join(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="exit 1 on drift (default)")
    group.add_argument(
        "--stop-hook",
        action="store_true",
        help="emit a Claude Code systemMessage JSON on drift, always exit 0",
    )
    args = parser.parse_args()

    ok, output = run_gates()

    if args.stop_hook:
        if ok:
            print(json.dumps({"suppressOutput": True}))
        else:
            print(
                json.dumps(
                    {
                        "systemMessage": "Repo guardrails found drift — fix before "
                        "wrapping up (also enforced at pre-push):\n" + output,
                        "suppressOutput": True,
                    }
                )
            )
        return 0

    # --check / default: hard gate.
    print(output)
    if not ok:
        print("\nrepo guardrails failed — see drift above", file=sys.stderr)
        return 1
    print("repo guardrails passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
