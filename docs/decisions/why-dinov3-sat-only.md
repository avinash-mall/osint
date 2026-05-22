# Why DINOv3-SAT Is the Only Embedding Model

## Decision

The single embedding shipped on every detection is **DINOv3 ViT-L SAT-493M** (`facebook/dinov3-vitl16-pretrain-sat493m`). The earlier `DINOV3_LVD` candidate was removed.

## Why

Benchmarks (see [benchmarks/embedding-stability.md](../benchmarks/embedding-stability.md), [benchmarks/video-tracking-stability.md](../benchmarks/video-tracking-stability.md)):

| Embedding | Stills (DOTA aug re-ID top-1) | Drone video SEP | Latency |
|---|---|---|---|
| DINOv3-SAT-L | **100%** | **+0.22** | 217 ms |
| DINOv3-LVD-L | NaN (silent failure on small crops) | — | 715 ms (2.5× slower) |

DINOV3_LVD emitted **NaN embeddings** on real drone-video crops — a silent failure that broke downstream cosine similarity without raising. Also 2.5× slower with no measured quality advantage on either still or video data. See [removed-dinov3-lvd.md](removed-dinov3-lvd.md).

DINOv3-SAT is specifically pretrained on satellite imagery (SAT-493M) — the dominant modality in the stack, and it dominates the re-ID workload (cross-pass detection linking).

## What this enables

- **Cross-image re-ID** — `/api/detections/{id}/similar` returns embedding-cosine nearest neighbors across all stored detections.
- **Cross-frame re-ID** — FMV tracks carry a single embedding per track (first frame); used by candidate-link scoring and the "Similar" panel.
- **Track linking re-run** — `POST /api/tracks/detections/reprocess` rebuilds tracks from stored embeddings without re-running the detector.

## Trade-offs accepted

- **Gated weights** — requires `HF_TOKEN` with approved access (Meta DINOv3 license).
- **Single embedding only** — no fall-back if DINOv3 hits OOM; the detection is still persisted, just without an `embedding` field. Code path: try DINOv3 → on failure log and skip, never raise.

## Cross-references

- [inference/dinov3-embeddings.md](../inference/dinov3-embeddings.md)
- [removed-dinov3-lvd.md](removed-dinov3-lvd.md)
- [benchmarks/embedding-stability.md](../benchmarks/embedding-stability.md)
- [benchmarks/video-tracking-stability.md](../benchmarks/video-tracking-stability.md)
