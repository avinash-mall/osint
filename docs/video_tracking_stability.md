# Video Tracking — Embedding Stability

Generated: 2026-05-10T17:38:15

## Methodology

For each drone video, extract 6 evenly-spaced frames with cv2; POST each frame to `/detect` with `enabled_layers=['sam3', '<EMBED_LAYER>']`; collect per-detection embeddings + bboxes.

Synthesise tracks by chaining detections across consecutive frames with bbox IoU ≥ 0.2 (greedy match). For each multi-frame track:
- **INTRA** = mean cosine similarity within the track (close to 1 = embedding is stable across frames).
- **INTER** = mean cosine similarity between tracks (lower = embeddings discriminate instances).
- **SEPARATION** = INTRA − INTER (higher is better; target ≥ 0.10 for useful re-ID).
- **Top-1 retrieval** = each track's primary embedding's nearest neighbour in the pool (excluding itself) is from the same track.

*This sidesteps `/detect_video`, which currently has SDK dependency issues (missing `flash_attn_interface`, multiplex `init_state` kwarg mismatches). The cv2-extracted-frames approach gives the same tracking-quality signal.*

## Results

| Video | Embed Layer | Tracks (multi-frame / total) | INTRA | INTER | **SEPARATION** | **Top-1** | Eval s |
|---|---|---|---|---|---|---|---|
| 53902-476396222_medium.mp4 | dinov3_sat | 35 / 295 | 0.872 ± 0.062 | 0.805 ± 0.120 | **+0.067** | **0.0%** | 12.2 |
| 53902-476396222_medium.mp4 | dinov3_lvd | 35 / 295 | nan ± nan | nan ± nan | **+nan** | **0.0%** | 11.4 |
| 168811-839864556_medium.mp4 | dinov3_sat | 21 / 133 | 0.898 ± 0.043 | 0.681 ± 0.151 | **+0.217** | **14.3%** | 15.1 |
| 168811-839864556_medium.mp4 | dinov3_lvd | 21 / 133 | nan ± nan | nan ± nan | **+nan** | **0.0%** | 15.2 |

## Interpretation

- **SEPARATION ≥ 0.10**: embedding usefully distinguishes tracked objects across frames. Useful for re-ID workflows.
- **Top-1 ≥ 70%**: nearest-neighbour matching reliably finds another frame of the same object.
- **DINOV3_SAT** is satellite-tuned (sat493m pretraining); **DINOV3_LVD** is FMV/video-tuned (lvd1689m). For drone-video tracking, the better performer should be preferred — see the SEPARATION column.
