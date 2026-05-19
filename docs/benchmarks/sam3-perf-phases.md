# SAM3 Performance Phase Tuning

**Raw outputs:** [bench/baseline.json](../../bench/baseline.json), [bench/optimized.json](../../bench/optimized.json), [bench/sam3_phaseA_baseline.json](../../bench/sam3_phaseA_baseline.json), [bench/sam3_phaseA3.json](../../bench/sam3_phaseA3.json), [bench/sam3_phaseBC.json](../../bench/sam3_phaseBC.json), [bench/sam3_after_profile_tuning.json](../../bench/sam3_after_profile_tuning.json)

## Purpose

Track the iterative tuning of SAM3's per-stage latency. Each `phase*.json` is a snapshot in the optimization sequence. The corresponding `bench/comparison.md` and `bench/sam3_comparison.md` files describe what changed between phases.

## How to read

Each JSON contains `metrics.<slug>.{p50_ms, p95_ms, count}` for stages: `encode`, `sam3`, `dota`, `gdino`, `dinov3`, `fusion`, `total`. Compare snapshots to see which stage moved.

Phase letters:

- **A baseline** — initial measurement.
- **A3** — TF32 + cuDNN benchmark tuned per GPU profile (set by `scripts/configure_host.py`).
- **B/C** — SDPA backend pinned, torch.compile attempts.
- **After profile tuning** — final operational baseline with current defaults.

## How to capture a new snapshot

```bash
cd inference-sam3
python benchmark_detect.py --url http://localhost:8001 --output ../bench/sam3_phase<X>.json
```

## Cross-references

- [inference/sam3-perf-profiling.md](../inference/sam3-perf-profiling.md)
- [scripts/benchmark-scripts.md](../scripts/benchmark-scripts.md)
- [deployment/gpu-profile-detection.md](../deployment/gpu-profile-detection.md)
