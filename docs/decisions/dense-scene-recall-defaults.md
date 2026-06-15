# Dense-scene recall defaults (NMS mode, YOLOE imgsz, small-object pass)

**Status:** shipped (2026-06-15). Shifts a small set of fusion / resolution /
chip-pass defaults toward recall in crowded scenes (ports, parking aprons, dense
vehicle yards), prompted by an external technical audit of the repo. All knobs stay
env-overridable — revert any one if precision or latency regresses. This does
**not** touch the precision-first *prompt* philosophy
([why-precision-first-inference-defaults.md](why-precision-first-inference-defaults.md));
it only changes post-detection dedup, YOLOE input size, and the chip-pass grid.

## Problem

The shipped code/compose defaults were tuned for throughput and de-duplication,
not for dense small-object scenes:

- Cross-tile NMS ran **class-agnostic + hard** (`SAM3_NMS_AGNOSTIC=1`,
  `SAM3_NMS_SOFT=0`), which cross-suppresses adjacent valid objects and
  specialist-vs-SAM3 boxes for neighbouring targets in crowded scenes.
- `YOLOE_IMGSZ=640` is small for distant / densely-packed FMV and aerial targets.
- The SAHI-style small-object second pass and edge-chip padding / `torch.compile`
  shipped **off** in the code defaults and `.env.example`, even though the tuned
  host had already enabled them.

## What changed

1. **NMS mode → per-class + soft.** `SAM3_NMS_AGNOSTIC 1→0`, `SAM3_NMS_SOFT 0→1`.
   Changed at the source so a regenerated host inherits it:
   [scripts/gpu_profiles.py](../../scripts/gpu_profiles.py) `runtime_env` (the
   value written into the `# BEGIN SENTINEL GENERATED GPU CONFIG` block of `.env`),
   plus the code fallback [inference-sam3/main.py](../../inference-sam3/main.py)
   `_nms_agnostic` / `_nms_soft`, `docker-compose.yml`, and `.env.example`.
   Soft-NMS decays overlapping detections by `(1 - mask_iou)` instead of dropping
   them — raises recall in dense scenes at the cost of more low-conf candidates
   downstream (the confidence policy floor still trims them).
2. **YOLOE imgsz 640 → 896.** [inference-sam3/yoloe.py](../../inference-sam3/yoloe.py)
   default + `.env.example`. ~1.96× pixels-per-object; /32-aligned; tunable to
   960/1024 for very dense scenes or back to 640 for speed.
3. **Promote already-validated chip-pass settings to shipped defaults** (these were
   already live on the tuned host via `.env`; this only fixes fresh-deploy +
   `.env.example` drift, so no behaviour change on the tuned host):
   - Small-object 2nd pass `INFERENCE_SMALL_OBJECT_CHIP_SIZE 0 → 504`
     ([backend/worker_legacy.py](../../backend/worker_legacy.py), `.env.example`).
   - Edge-chip padding `INFERENCE_PAD_CHIPS_TO_SIZE False → True` and image
     `torch.compile` `SAM3_COMPILE_IMAGE 0 → 1` — both already decided in
     [sam3-compile-and-chip-padding-2026-06-14.md](sam3-compile-and-chip-padding-2026-06-14.md);
     here we align the code defaults, compose, and `.env.example` to that decision.

## Tradeoffs (accepted)

- **Precision / candidate volume:** soft + per-class NMS surface more low-conf and
  near-duplicate candidates; the `GLOBAL_CONFIDENCE_FLOOR` + per-class floors absorb
  most of it. Watch precision on sparse wide-area scenes — flip back there.
- **Compute:** the small-object pass is the highest-cost knob (~+1 inference pass
  per scene). `torch.compile` pays a one-time ~30–40 s warmup. `YOLOE_IMGSZ=896` is
  slower per frame than 640. None of these are free; they trade throughput for recall.

## What was deliberately NOT changed

- `SAM3_SERIALIZE_FORWARDS=1` / `WEB_CONCURRENCY=1` — a documented A100/sm_80 +
  CUDA 13.x CUDA-context-corruption safeguard (`docker-compose.yml`), not a tuning
  knob. Relax only on profiled-safe hardware.
- DOTA-OBB prompt-relevance gating and the `0.20` valid-pixel-fraction floor (with
  per-class overrides) — deliberate, configurable, left as-is.
- The precision-first prompt resolution (ontology-scoped vocabulary).

## Supersedes

The small-object second pass shipped **opt-in / off by default** per
[multi-scale-and-full-scene-chip-passes.md](multi-scale-and-full-scene-chip-passes.md).
This decision flips its shipped default **on** (`504`) for dense-scene recall; that
doc's mechanism is unchanged, only the default. The full-scene pass stays off.

## Rollback (per knob, via env)

`SAM3_NMS_AGNOSTIC=1`, `SAM3_NMS_SOFT=0`, `YOLOE_IMGSZ=640`,
`INFERENCE_SMALL_OBJECT_CHIP_SIZE=0`, `INFERENCE_PAD_CHIPS_TO_SIZE=0`,
`SAM3_COMPILE_IMAGE=0`.

## Applying the NMS change on a tuned host

The NMS values live in the configure_host-generated `.env` block (never hand-edit
it — [agent-entry hard rules](../agent-entry.md)). After taking this change, run
`python scripts/configure_host.py` on the deployment host to regenerate the block,
then restart `inference-sam3`. `python scripts/configure_host.py --dry-run` should
show `SAM3_NMS_AGNOSTIC=0` / `SAM3_NMS_SOFT=1`.

## Verification

Quantify before/after on a dense chip with the repo benchmark:
`python inference-sam3/benchmark_detect.py --url http://localhost:8001 --chip <dense>.png --iters 100 --warmup 5 --prompts "ship,aircraft,vehicle,building"`.
Compare detection counts, `debug_counts.suppressed_by_nms`, and `timings_ms`.

## Cross-references

- [why-precision-first-inference-defaults.md](why-precision-first-inference-defaults.md) — the prompt-side philosophy this does not change
- [sam3-compile-and-chip-padding-2026-06-14.md](sam3-compile-and-chip-padding-2026-06-14.md) — compile + padding, now promoted to defaults
- [multi-scale-and-full-scene-chip-passes.md](multi-scale-and-full-scene-chip-passes.md) — small-object pass mechanism (default reversed here)
- [inference/fusion-and-nms.md](../inference/fusion-and-nms.md) — `mask_aware_nms`, agnostic/soft modes
- [inference/yoloe-tracker.md](../inference/yoloe-tracker.md) — `YOLOE_IMGSZ`
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
- [scripts/configure-host-gpu.md](../scripts/configure-host-gpu.md) — generated `.env` block
