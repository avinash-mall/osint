#!/usr/bin/env python3
"""PreToolUse guard: block Edit/Write/MultiEdit to banned runtime/baked paths.

``docs/agent-entry.md`` (Hard Rules) forbids writing generated runtime data and
baked model artifacts into the working tree on a dev host. This script turns
that "please don't" into an enforced block so an agent cannot accidentally
clobber a 150 GB host bind-mount or commit a baked weight.

Wired as a Claude Code ``PreToolUse`` hook on Edit|Write|MultiEdit|NotebookEdit.
It reads the hook JSON on stdin, extracts the target path, and exits 2 (block,
with the reason fed back to the agent) when the path is banned; exit 0 allows.

Banned (verbatim from agent-entry.md Hard Rules):
    /data/*                          host runtime data dirs
    bench/                           benchmark run artifacts
    assets/static/basemap/           baked basemap tiles
    inference-sam3/yolo*.pt          baked YOLO/YOLOE weights
    inference-sam3/yoloe-*.pt
    inference-sam3/mobileclip2_b.ts  baked MobileCLIP2 text encoder

Self-test (no stdin):
    python scripts/forbidden_paths_guard.py --path bench/x.json   # -> blocked, exit 2
    python scripts/forbidden_paths_guard.py --path backend/x.py   # -> allowed, exit 0
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Repo-relative glob patterns (matched against the path relative to repo root).
RELATIVE_BANS = (
    "bench/**",
    "assets/static/basemap/**",
    "inference-sam3/yolo*.pt",
    "inference-sam3/yoloe-*.pt",
    "inference-sam3/mobileclip2_b.ts",
)
# Absolute prefixes (host runtime mounts that live outside the repo).
ABSOLUTE_BANS = ("/data/",)

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def extract_path(tool_input: dict) -> str | None:
    for key in ("file_path", "notebook_path", "path"):
        val = tool_input.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def banned_reason(raw_path: str) -> str | None:
    """Return a human reason if raw_path is banned, else None."""
    p = Path(raw_path)
    abs_path = (p if p.is_absolute() else (ROOT / p)).resolve()
    abs_str = abs_path.as_posix()

    for prefix in ABSOLUTE_BANS:
        if abs_str == prefix.rstrip("/") or abs_str.startswith(prefix):
            return f"writes under host runtime dir '{prefix}'"

    try:
        rel = abs_path.relative_to(ROOT).as_posix()
    except ValueError:
        return None  # outside the repo and not an absolute ban — not our concern

    for pattern in RELATIVE_BANS:
        # fnmatch '*' does not cross '/', so match the '**' dir bans on the prefix.
        if pattern.endswith("/**"):
            base = pattern[:-3]
            if rel == base or rel.startswith(base + "/"):
                return f"writes baked/runtime artifact path '{base}/'"
        elif fnmatch.fnmatch(rel, pattern):
            return f"writes baked artifact matching '{pattern}'"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", help="self-test: check this path instead of reading stdin")
    args = parser.parse_args()

    if args.path is not None:
        reason = banned_reason(args.path)
        if reason:
            print(f"BLOCK: {args.path} {reason}", file=sys.stderr)
            return 2
        print(f"OK: {args.path}")
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # malformed/no payload: fail open, never wedge the agent

    if payload.get("tool_name") not in EDIT_TOOLS:
        return 0

    raw_path = extract_path(payload.get("tool_input") or {})
    if not raw_path:
        return 0

    reason = banned_reason(raw_path)
    if reason:
        print(
            f"Blocked by forbidden_paths_guard: '{raw_path}' {reason}. "
            "This path is a baked/runtime artifact per docs/agent-entry.md Hard "
            "Rules — do not write it from the dev host. Rebuild it through its "
            "baker instead.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
