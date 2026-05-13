#!/usr/bin/env python3
"""FMV end-to-end performance benchmark.

Uploads each clip via /api/fmv/clips in each configured prompt_mode,
polls the tracking status until complete (or failed), and reports
wall-clock + detection counts to stdout and CSV.

Pure black-box timing through the production HTTP route — no GPU
instrumentation needed.

Usage:
    python scripts/bench_fmv.py clip1.mp4 [clip2.mp4 ...]

Optional env:
    SENTINEL_API_URL=http://localhost:3000  (nginx proxy; default)
    BENCH_TIMEOUT_S=1800                    (per-clip timeout)
    BENCH_MODES=pcs                         (which modes to test, comma sep)
    BENCH_OUT_DIR=/tmp                      (where to write CSV)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

API_URL = os.getenv("SENTINEL_API_URL", "http://localhost:3000")
TIMEOUT_S = int(os.getenv("BENCH_TIMEOUT_S", "1800"))
MODES = [m.strip() for m in os.getenv("BENCH_MODES", "pcs").split(",") if m.strip()]
OUT_DIR = Path(os.getenv("BENCH_OUT_DIR", "/tmp"))


def _upload(session: requests.Session, clip_path: Path, mode: str) -> dict[str, Any]:
    with clip_path.open("rb") as fh:
        files = {"file": (clip_path.name, fh, "video/mp4")}
        data = {"name": f"bench {clip_path.stem} {mode}", "prompt_mode": mode}
        resp = session.post(f"{API_URL}/api/fmv/clips", files=files, data=data, timeout=120)
    resp.raise_for_status()
    return resp.json()["clip"]


def _poll_until_done(session: requests.Session, clip_id: int, timeout_s: int) -> dict[str, Any]:
    """Poll /api/fmv/clips/<id> until tracking_status is complete or failed."""
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        resp = session.get(f"{API_URL}/api/fmv/clips", timeout=10)
        resp.raise_for_status()
        clips = resp.json().get("clips", [])
        for clip in clips:
            if int(clip["id"]) == clip_id:
                last = clip
                status = (clip.get("metadata") or {}).get("tracking_status")
                if status in {"complete", "failed", "cancelled"}:
                    return clip
                break
        time.sleep(2)
    last["__timeout__"] = True
    return last


def _bench_clip(session: requests.Session, clip_path: Path, mode: str) -> dict[str, Any]:
    print(f"[bench] uploading {clip_path.name} mode={mode}", flush=True)
    start = time.monotonic()
    clip = _upload(session, clip_path, mode)
    clip_id = int(clip["id"])
    print(f"[bench] clip_id={clip_id} queued — polling…", flush=True)
    final = _poll_until_done(session, clip_id, TIMEOUT_S)
    elapsed = time.monotonic() - start
    meta = final.get("metadata") or {}
    return {
        "clip": clip_path.name,
        "mode": mode,
        "clip_id": clip_id,
        "windows": int(meta.get("tracking_windows") or 0),
        "wall_s": round(elapsed, 2),
        "detections": int(meta.get("tracking_count") or 0),
        "status": str(meta.get("tracking_status") or "unknown"),
        "fps_window": round(int(meta.get("tracking_windows") or 0) / elapsed, 3) if elapsed > 0 else 0.0,
        "error": str(meta.get("tracking_error") or ""),
    }


def _print_table(rows: list[dict[str, Any]]) -> None:
    headers = ["clip", "mode", "windows", "wall_s", "detections", "status", "fps_window"]
    widths = {h: max(len(h), max((len(str(r.get(h, ""))) for r in rows), default=0)) for h in headers}
    print("  ".join(f"{h:<{widths[h]}}" for h in headers))
    print("  ".join("-" * widths[h] for h in headers))
    for r in rows:
        print("  ".join(f"{str(r.get(h, '')):<{widths[h]}}" for h in headers))


def _write_csv(rows: list[dict[str, Any]]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S")
    out_path = OUT_DIR / f"fmv_bench_{ts}.csv"
    if not rows:
        return out_path
    keys = list(rows[0].keys())
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clips", nargs="+", type=Path, help="clip files to benchmark")
    args = parser.parse_args()

    for clip in args.clips:
        if not clip.is_file():
            print(f"[bench] error: {clip} not found", file=sys.stderr)
            return 2

    print(f"[bench] API_URL={API_URL} modes={MODES} timeout_s={TIMEOUT_S}", flush=True)
    session = requests.Session()
    rows: list[dict[str, Any]] = []
    for clip in args.clips:
        for mode in MODES:
            try:
                row = _bench_clip(session, clip, mode)
            except Exception as exc:
                row = {
                    "clip": clip.name, "mode": mode, "clip_id": -1, "windows": 0,
                    "wall_s": 0.0, "detections": 0, "status": "exception",
                    "fps_window": 0.0, "error": str(exc)[:200],
                }
            rows.append(row)
            print(f"[bench] {clip.name} {mode}: {row['wall_s']}s, "
                  f"{row['detections']} dets, status={row['status']}", flush=True)

    print()
    _print_table(rows)
    csv_path = _write_csv(rows)
    print(f"\n[bench] wrote {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
