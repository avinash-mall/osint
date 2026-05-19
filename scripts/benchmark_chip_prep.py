"""Benchmark the raster chip-prep pipeline against a fixture COG.

Drives `worker.slice_and_infer` end-to-end (it talks to a running
inference-sam3 instance at $INFERENCE_SAM3_URL), captures per-stage
latency histograms via `chip_prep_profiler`, and writes a JSON summary
that mirrors the shape of `bench/sam3_phaseA_baseline.json` so the
existing comparison tooling (`bench/sam3_comparison.md` workflow) can
read it.

Usage:
    INFERENCE_SAM3_URL=http://localhost:8001 \
    CHIP_PREP_BENCH_FIXTURE=/data/imagery/processed/sample_cog.tif \
        python scripts/benchmark_chip_prep.py \
            --label baseline \
            --output bench/raster_chip_prep_baseline.json

A single iteration over the full grid is the default; multiple iterations
average across passes to stabilise tail percentiles. The chip-prep loop
already iterates many windows per run, so individual chips are the
statistical unit — extra iterations only help when the raster is tiny.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path


def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    sorted_samples = sorted(samples)
    idx = min(len(sorted_samples) - 1, int(round(pct / 100.0 * len(sorted_samples))))
    return sorted_samples[idx]


def _summarise(samples: list[float]) -> dict:
    if not samples:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "mean_ms": 0.0, "stdev_ms": 0.0, "count": 0}
    return {
        "p50_ms": round(_percentile(samples, 50), 3),
        "p95_ms": round(_percentile(samples, 95), 3),
        "p99_ms": round(_percentile(samples, 99), 3),
        "mean_ms": round(statistics.fmean(samples), 3),
        "stdev_ms": round(statistics.pstdev(samples), 3) if len(samples) > 1 else 0.0,
        "count": len(samples),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        default=os.getenv("CHIP_PREP_BENCH_FIXTURE"),
        help="Path to a Cloud-Optimized GeoTIFF to slice. Falls back to $CHIP_PREP_BENCH_FIXTURE.",
    )
    parser.add_argument("--url", default=os.getenv("INFERENCE_SAM3_URL", "http://localhost:8001"))
    parser.add_argument("--label", default="run", help="Filename suffix when --output not given.")
    parser.add_argument("--output", default=None, help="JSON output path. Defaults to bench/raster_chip_prep_<label>.json.")
    parser.add_argument(
        "--prompts",
        default="ship,plane,vehicle,building",
        help="Comma-separated open-vocab prompts handed to /detect.",
    )
    parser.add_argument("--iters", type=int, default=1, help="Number of full slice_and_infer passes to run.")
    parser.add_argument(
        "--max-chips",
        type=int,
        default=None,
        help="Override MAX_INFERENCE_CHIPS for this run (useful for fast smoke benches).",
    )
    args = parser.parse_args()

    if not args.fixture:
        parser.error("--fixture (or $CHIP_PREP_BENCH_FIXTURE) is required")
    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        parser.error(f"fixture not found: {fixture_path}")

    # MUST be set before importing worker_legacy so chip_prep_profiler picks
    # up the env at module-load time.
    os.environ["CHIP_PREP_PROFILE"] = "1"
    os.environ["INFERENCE_SAM3_URL"] = args.url
    if args.max_chips is not None:
        os.environ["MAX_INFERENCE_CHIPS"] = str(args.max_chips)

    # backend/ must be importable; we don't depend on a real Celery worker
    # process — slice_and_infer is a plain function.
    repo_root = Path(__file__).resolve().parent.parent
    backend_dir = repo_root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    import chip_prep_profiler  # noqa: E402
    from worker_legacy import slice_and_infer  # noqa: E402

    if not chip_prep_profiler.is_enabled():
        # In case worker_legacy was already loaded earlier (e.g. via a sys.modules
        # cache from a previous test in the same process), flip the flag.
        chip_prep_profiler.force_enable_for_tests()

    prompts = [p.strip() for p in args.prompts.split(",") if p.strip()]
    inference_metadata = {"prompts": prompts}

    iteration_summaries: list[dict] = []
    overall_started = time.perf_counter()

    for it in range(args.iters):
        chip_prep_profiler.reset()
        t_iter = time.perf_counter()
        detections, summary = slice_and_infer(
            str(fixture_path),
            pass_id=10_000 + it,
            inference_metadata=inference_metadata,
            on_chip_store=lambda _kept, _idx: None,
        )
        iter_elapsed_s = time.perf_counter() - t_iter
        snap = chip_prep_profiler.snapshot()
        per_stage = {stage: _summarise(values) for stage, values in snap.items()}
        iteration_summaries.append(
            {
                "iteration": it,
                "elapsed_s": round(iter_elapsed_s, 3),
                "chips_per_s": round(summary.get("processed_chips", 0) / iter_elapsed_s, 3)
                if iter_elapsed_s > 0
                else 0.0,
                "summary": {
                    k: summary.get(k)
                    for k in (
                        "chip_size",
                        "overlap",
                        "step",
                        "planned_chips",
                        "source_total_chips",
                        "processed_chips",
                        "failed_chips",
                        "raw_detections",
                        "deduped_detections",
                        "suppressed_detections",
                        "inference_speed_profile",
                        "max_pending_chips",
                        "dedupe_method",
                        "multi_scale",
                    )
                    if k in summary
                },
                "stages": per_stage,
            }
        )

    overall_elapsed = time.perf_counter() - overall_started

    # Aggregate across iterations: pool all stage samples then re-summarise.
    pooled: dict[str, list[float]] = {}
    for it_summary in iteration_summaries:
        # Reconstruct from per-iteration stage means is lossy; instead re-pool
        # by re-snapshotting the last iteration's raw histogram (already in
        # iteration_summaries as percentiles, but for true pooling we want the
        # raw samples). Simpler: re-run snapshot one last time which contains
        # only the final iteration. For multi-iter aggregation we report per-iter.
        pass
    final_snap = chip_prep_profiler.snapshot()
    aggregate_stages = {stage: _summarise(values) for stage, values in final_snap.items()}

    out = {
        "label": args.label,
        "url": args.url,
        "fixture": str(fixture_path),
        "iters": args.iters,
        "overall_elapsed_s": round(overall_elapsed, 3),
        "iterations": iteration_summaries,
        "aggregate_last_iter": aggregate_stages,
        "prompts": prompts,
    }

    output_path = (
        Path(args.output)
        if args.output
        else repo_root / "bench" / f"raster_chip_prep_{args.label}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    print(f"\nWrote {output_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
