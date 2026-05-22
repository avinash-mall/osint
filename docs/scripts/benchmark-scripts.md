# Benchmark Scripts — FMV, Inference Latency

**Paths:**
- [scripts/bench_fmv.py](../../scripts/bench_fmv.py)
- [scripts/benchmark_inference.sh](../../scripts/benchmark_inference.sh)
- [scripts/benchmark_sam3_inference.py](../../scripts/benchmark_sam3_inference.py)
- [scripts/embedding_stability.py](../../scripts/embedding_stability.py)
- [scripts/video_tracking_stability.py](../../scripts/video_tracking_stability.py)
- [scripts/run_video_profile.sh](../../scripts/run_video_profile.sh)
- [inference-sam3/benchmark_detect.py](../../inference-sam3/benchmark_detect.py)

## bench_fmv.py

End-to-end FMV perf: posts clips via `/api/fmv/clips` in each prompt mode (`pcs`, `yoloe`, `yoloe-pf`), polls until completion, reports wall-clock + detection counts.

```bash
python scripts/bench_fmv.py --backend http://localhost:3000 --clip sample/53902-476396222_medium.mp4
```

## benchmark_inference.sh + benchmark_sam3_inference.py

Lightweight chip-only latency probe. Generates synthetic RGB chips, measures `/detect` latency across sizes.

```bash
bash scripts/benchmark_inference.sh
# or:
python scripts/benchmark_sam3_inference.py --url http://172.18.0.2:8001 --chip-size 1008
```

## embedding_stability.py

Augmentation-based re-ID on still chips — see [benchmarks/embedding-stability.md](../benchmarks/embedding-stability.md).

## video_tracking_stability.py

Drone-video cross-frame re-ID — see [benchmarks/video-tracking-stability.md](../benchmarks/video-tracking-stability.md).

## run_video_profile.sh

Temporarily reconfigures `inference-sam3` to the video profile (SAM3 image + SAM3 video + DINOv3 only), runs a benchmark command, restores the original config. Useful when running a video benchmark on a host normally serving imagery.

## inference-sam3/benchmark_detect.py

Standalone `/detect` benchmark used to record [bench/sam3_phase*.json](../../bench/) snapshots — see [benchmarks/sam3-perf-phases.md](../benchmarks/sam3-perf-phases.md).

```bash
cd inference-sam3
python benchmark_detect.py --url http://localhost:8001 --output ../bench/sam3_phaseX.json
```

## Cross-references

- [testing/benchmark-harness.md](../testing/benchmark-harness.md)
- [benchmarks/embedding-stability.md](../benchmarks/embedding-stability.md)
- [benchmarks/video-tracking-stability.md](../benchmarks/video-tracking-stability.md)
- [benchmarks/sam3-perf-phases.md](../benchmarks/sam3-perf-phases.md)
