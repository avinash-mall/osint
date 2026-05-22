# Video Tracking Stability — Drone Footage Re-ID

**Source:** [scripts/video_tracking_stability.py](../../scripts/video_tracking_stability.py)
**Dataset:** NASA-published 1440p drone footage from `sample/`
**Layer:** `dinov3_sat`

## What it measures

Cross-frame re-ID quality. For each tracked object across `N_FRAMES` frames, compute embedding cosine similarity. **SEP** = mean(intra-track sim) − mean(inter-track sim). Higher SEP = better tracking.

## Result

| Embedding | SEP | Latency |
|---|---|---|
| **DINOv3-SAT-L** | **+0.22** | 217 ms |
| DINOv3-LVD-L (removed) | NaN on small crops | 715 ms (2.5× slower) |

DINOV3_LVD was removed for silent NaN failures — see [decisions/removed-dinov3-lvd.md](../decisions/removed-dinov3-lvd.md). SAT is faster AND better on drone footage.

## How to reproduce

```bash
python scripts/video_tracking_stability.py \
  --url http://172.18.0.2:8001 \
  --videos sample/53902-476396222_medium.mp4,sample/168811-839864556_medium.mp4 \
  --prompts car,vehicle,person,truck \
  --n-frames 6 --iou-threshold 0.2 --layers dinov3_sat
```

## Cross-references

- [inference/dinov3-embeddings.md](../inference/dinov3-embeddings.md)
- [backend/fmv-track-consolidation.md](../backend/fmv-track-consolidation.md)
- [decisions/why-dinov3-sat-only.md](../decisions/why-dinov3-sat-only.md)
- [decisions/removed-dinov3-lvd.md](../decisions/removed-dinov3-lvd.md)
