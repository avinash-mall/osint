# Defer DINOv3 embedding to after fusion (embed survivors only) — 2026-06-15

## Context

Once `SAM3_COMPILE_IMAGE=1` cut the SAM3 text decode from ~1450 ms to ~195 ms/chip
(see [sam3-compile-and-chip-padding-2026-06-14.md](sam3-compile-and-chip-padding-2026-06-14.md)),
the per-chip hot path shifted to the **DINOv3-SAT crop embedding** (~147 ms/chip
mean) and OBB postprocess (~59 ms). Profiling the full 2000-chip `al_udeid_z19_esri`
re-ingest exposed where the embedding time went:

| | per chip (mean) | scene total |
|---|---|---|
| candidates (pre-NMS) | 45.7 | 91,485 |
| detections (post-NMS) | 28.0 | 56,031 |
| **suppressed by fusion** | **17.7** | **35,454 (38.8%)** |

The embedding pass ran in the candidate loop **before** `fuse_detections`, so it
embedded all 91,485 candidates and then fusion discarded 38.8% of them —
**~38% of the embedding compute (~110 s of the run) was thrown away.**

## Decision

Move the `embed_crops_batched` call in `_detect_pipeline`
([inference-sam3/main.py](../../inference-sam3/main.py)) to run **after** WBF/NMS,
over the surviving detections only. The per-survivor embed box is reconstructed
with `fusion._xyxy_from_detection(det)` — the same det→pixel-box inverse NMS
already uses — instead of the parallel `embed_bboxes` list collected pre-fusion
(now removed).

## Why this is byte-identical, not a quality trade

The embedded crop (and therefore the 1024-D vector) of every *surviving* detection
is unchanged from the pre-fusion order, for every fusion mode:

- **WBF (default):** a survivor is `dict(highest_confidence_member)` with only
  `confidence` overridden ([fusion.py `wbf_fusion`](../../inference-sam3/fusion.py#L412)).
  The averaged fused box is used *only* to cluster members; the output keeps the
  member's original `bbox`/`mask` geometry. So `_xyxy_from_detection(survivor)`
  reproduces exactly the `bbox_xyxy` that member would have been embedded at.
- **Hard NMS / `SAM3_FUSION_MODE=nms`:** survivors are the unmodified input dicts.
- **Soft-NMS (`SAM3_NMS_SOFT=1`):** keeps every detection (decays confidence, never
  drops), so it embeds the same set as before — no change, no saving.
- Fusion never reads `embedding`, so deferring it cannot alter which detections
  survive. `embed_crop` re-rounds the box to int pixels, absorbing float
  round-trip noise from the normalize/denormalize.

Proven by `test_deferred_embed_box_is_member_box_not_fused_box` and
`test_deferred_embed_box_matches_nms_survivor_box` in
[test_fusion_wbf.py](../../inference-sam3/tests/test_fusion_wbf.py): the survivor's
deferred embed box equals its winning member's box and is **not** the WBF averaged
box.

## Measured effect

Real-chip microbench (36 windows sampled across the `al_udeid` COG, warm graph):

| | old (pre-fusion embed) | new (post-fusion embed) |
|---|---|---|
| crops embedded / chip | all candidates (~46) | survivors only (~32) |
| **embedding / chip** | ~147 ms (median 114) | **~95 ms (median 57)** |
| per embedded crop | ~3 ms | ~3 ms (unchanged) |
| suppressed share | — | 35% fewer crops |

The per-crop cost is unchanged; the win is purely the ~35–39% of crops no longer
embedded. Across the 2000-chip scene that is ≈ −100 s off the ~1696 s full-coverage
run (~6%). Savings scale with the fusion suppression ratio — large on dense scenes
(ports, airfields, parking), negligible on sparse ones. No effect when
`SAM3_EMBED_DETECTIONS=0`.

## Validated at scale (2026-06-15)

Full-coverage re-ingest of `al_udeid_z19_esri` (15104², 2000 chips, multi-scale)
with both this change and `SAM3_COMPILE_IMAGE=1` live:

- **Output identical.** 26,163 detections (vs 26,165 pre-change, −0.008%); same
  candidate/suppression structure (91,483 candidates, 35,453 suppressed by NMS);
  same confidence distribution (avg 0.1518, 6,087 ≥0.2). An adversarial verifier
  recomputed the embedding mean, suppression ratio, and detection counts from raw
  logs and could not refute the change.
- **Embedding down.** Embedded crops dropped from ~46 (all candidates) to ~28
  (survivors), −38.7%. Worker per-chip `post_roundtrip` fell from 949.9 ms
  (compile-only) to ~817–844 ms (~−12%, de-cumulated from the process-cumulative
  `chip_prep_profiler`). The unaffected stages confirm the reorder is the only
  delta: `sam3_batched_forward` ~195 ms, `sam3_encode_image` ~91 ms, `postprocess`
  ~59 ms all unchanged.
- **Whole-scene wall-time ~20 min (1198 s) on a clean store.** A measurement
  caveat worth recording: total task wall-time is dominated NOT by GPU inference
  but by the post-inference **detection-tracker** stage (`update_tracks_for_pass`,
  [worker_legacy.py](../../backend/worker_legacy.py)), which associates the pass's
  detections against the existing track/detection store and so scales with
  *resident* detection count — measured ~4 s on an empty store vs ~322 s (12k
  resident) vs ~618 s (26k resident). The inference speedup only shows up in total
  wall-time on a small/clean store; isolate the chip POST→dedupe phase to see it.

## Cross-references

- [optical-inference-throughput.md](optical-inference-throughput.md) — the batched-embedding + ROI-postprocess hot-path work this builds on
- [sam3-compile-and-chip-padding-2026-06-14.md](sam3-compile-and-chip-padding-2026-06-14.md) — the decode-compile change that surfaced embedding as the new bottleneck
- [inference/dinov3-embeddings.md](../inference/dinov3-embeddings.md) · [inference/fusion-and-nms.md](../inference/fusion-and-nms.md)
- [why-embeddings-not-layer-gated.md](why-embeddings-not-layer-gated.md) — embedding is enrichment, runs regardless of `enabled_layers`
