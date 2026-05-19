"""Per-chip profile dump for the raster chip-prep pipeline.

Twin of `benchmark_chip_prep.py` but emits the per-event stream to a CSV
(via `chip_prep_profiler.open_csv`) for offline tools (pandas, gnuplot)
instead of summarising to JSON. The CSV is written to /tmp/ by default
because per-chip rows would clutter the bench/ canonical directory.

Usage:
    python scripts/profile_chip_prep.py \
        --fixture /data/imagery/processed/sample_cog.tif \
        --csv /tmp/chip_prep_events.csv
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default=os.getenv("CHIP_PREP_BENCH_FIXTURE"))
    parser.add_argument("--url", default=os.getenv("INFERENCE_SAM3_URL", "http://localhost:8001"))
    parser.add_argument("--csv", default="/tmp/chip_prep_events.csv")
    parser.add_argument("--prompts", default="ship,plane,vehicle,building")
    parser.add_argument("--max-chips", type=int, default=None)
    args = parser.parse_args()

    if not args.fixture:
        parser.error("--fixture (or $CHIP_PREP_BENCH_FIXTURE) is required")
    if not Path(args.fixture).exists():
        parser.error(f"fixture not found: {args.fixture}")

    os.environ["CHIP_PREP_PROFILE"] = "1"
    os.environ["INFERENCE_SAM3_URL"] = args.url
    if args.max_chips is not None:
        os.environ["MAX_INFERENCE_CHIPS"] = str(args.max_chips)

    repo_root = Path(__file__).resolve().parent.parent
    backend_dir = repo_root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    import chip_prep_profiler  # noqa: E402
    from worker_legacy import slice_and_infer  # noqa: E402

    chip_prep_profiler.force_enable_for_tests()
    chip_prep_profiler.reset()
    chip_prep_profiler.open_csv(args.csv)
    try:
        _, summary = slice_and_infer(
            args.fixture,
            pass_id=20_000,
            inference_metadata={"prompts": [p.strip() for p in args.prompts.split(",") if p.strip()]},
            on_chip_store=lambda _k, _i: None,
        )
    finally:
        chip_prep_profiler.close_csv()

    snap = chip_prep_profiler.snapshot()
    print(f"Wrote {args.csv}")
    print(f"Per-stage sample counts: {{ {', '.join(f'{k}={len(v)}' for k, v in snap.items())} }}")
    print(f"Processed chips: {summary.get('processed_chips')}  Failed: {summary.get('failed_chips')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
