# Benchmark Harness

## Components

| Script | Purpose | Doc |
|---|---|---|
| [scripts/fetch_real_datasets.py](../../scripts/fetch_real_datasets.py) | Download the DOTA-v1.0 val slice from HF | [scripts/fetch-eval-datasets.md](../scripts/fetch-eval-datasets.md) |
| [scripts/fetch_eval_datasets.py](../../scripts/fetch_eval_datasets.py) | Idempotent dataset prep with synthetic-fixture mode | [scripts/fetch-eval-datasets.md](../scripts/fetch-eval-datasets.md) |
| [scripts/compare_inference_layers.py](../../scripts/compare_inference_layers.py) | Main 7-layer comparison harness | [scripts/compare-inference-layers.md](../scripts/compare-inference-layers.md) |
| [scripts/embedding_stability.py](../../scripts/embedding_stability.py) | Augmentation-based re-ID on stills | [scripts/benchmark-scripts.md](../scripts/benchmark-scripts.md) |
| [scripts/video_tracking_stability.py](../../scripts/video_tracking_stability.py) | Drone-video re-ID across frames | [scripts/benchmark-scripts.md](../scripts/benchmark-scripts.md) |
| [scripts/eval_sar_cfar.py](../../scripts/eval_sar_cfar.py) | SAR CFAR ship detection on synthetic data | [scripts/eval-runners.md](../scripts/eval-runners.md) |
| [scripts/measure_calibration_ece.py](../../scripts/measure_calibration_ece.py) | Per-detector ECE pre/post temperature scaling | [scripts/eval-runners.md](../scripts/eval-runners.md) |
| [scripts/eval_candidate_links.py](../../scripts/eval_candidate_links.py) | Candidate-link scoring P/R on curated GT | [scripts/eval-runners.md](../scripts/eval-runners.md) |
| [scripts/bench_fmv.py](../../scripts/bench_fmv.py) | FMV end-to-end perf | [scripts/benchmark-scripts.md](../scripts/benchmark-scripts.md) |
| [inference-sam3/benchmark_detect.py](../../inference-sam3/benchmark_detect.py) | Standalone `/detect` latency/throughput | [scripts/benchmark-scripts.md](../scripts/benchmark-scripts.md) |

## Per-slice test datasets

| Slice | Source | Size | What it measures |
|---|---|---|---|
| `dota` | `Last-Bullet/DOTAv1.0` val (HF) | 30 chips, 1619 GT boxes | Box quality (mAP@0.5, per-class P/R/F1) |
| `sar` | Synthetic 2-band dB-range TIFFs | 10 chips | TerraMind latency overhead |
| `embedding` | DOTA chips, embedding latency only | 30 chips | DINOv3-SAT and TerraMind total/embed times |

## Quick run

```bash
# 1. Pull real datasets (synthetic fallback with --synthetic-fixtures)
python scripts/fetch_real_datasets.py
python scripts/fetch_eval_datasets.py

# 2. Main comparison
python scripts/compare_inference_layers.py \
  --url http://172.18.0.2:8001 \
  --slice all --max-chips 30 --repeats 3 \
  --output docs/benchmarks/inference-layer-comparison.md \
  --json-output docs/benchmarks/inference-layer-comparison.json \
  --restart-cmd "docker restart osint-inference-sam3-1" \
  --restart-wait-timeout 180 \
  --force-grounding-dino

# 3. Augmentation re-ID
python scripts/embedding_stability.py \
  --url http://172.18.0.2:8001 \
  --max-chips 8 --max-instances 15 --n-aug 4 --layers dinov3_sat

# 4. Drone video re-ID
python scripts/video_tracking_stability.py \
  --url http://172.18.0.2:8001 \
  --videos sample/53902-476396222_medium.mp4,sample/168811-839864556_medium.mp4 \
  --prompts car,vehicle,person,truck \
  --n-frames 6 --iou-threshold 0.2 --layers dinov3_sat
```

Pass `--dry-run` to verify report generation without a live service.

## Cross-references

- [benchmarks/inference-layer-comparison.md](../benchmarks/inference-layer-comparison.md)
- [scripts/compare-inference-layers.md](../scripts/compare-inference-layers.md)
- [scripts/benchmark-scripts.md](../scripts/benchmark-scripts.md)
