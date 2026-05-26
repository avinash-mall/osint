# Imagery upload mirrors the FMV `(model, prompt_mode)` selector

**Path:** [backend/routers/ingest.py](../../backend/routers/ingest.py), [frontend/src/components/IngestConnect.tsx](../../frontend/src/components/IngestConnect.tsx), [inference-sam3/main.py](../../inference-sam3/main.py)
**Lines touched:** ~110
**Depends on:** the prior FMV `(model, prompt_mode)` machinery at [main.py:1026-1054](../../backend/main.py#L1026-L1054), the bf16-cast YOLOE fix in [yoloe.py:210-226](../../inference-sam3/yoloe.py#L210-L226)

## Decision

The imagery upload form (IngestConnect → `POST /api/ingest/upload`) now accepts the same `model` + `prompt_mode` form fields as the FMV upload form, with the same semantics:

- `model=sam3 + prompt_mode=pcs` (default) — today's multi-layer fusion pipeline (SAM3 + DOTA-OBB + Grounding-DINO + Prithvi + DINOv3-SAT etc.), unchanged.
- `model=yolo26 + prompt_mode=amg` — YOLOE-PF only, per chip. SAM3 and the other specialists are skipped.
- `model=yolo26 + prompt_mode=pcs` — YOLOE-SEG with the analyst-supplied `text_prompts` (falling back to `FMV_FALLBACK_PROMPTS = ["vehicle","person","building"]` if the picker is empty).
- `model=sam3 + prompt_mode=amg` — rejected `HTTP 400` (mirror of the FMV constraint at [backend/main.py:1032-1036](../../backend/main.py#L1032-L1036)).

Plumbing layers:

1. **Frontend** — `IngestConnect.tsx` grew `imageryModel` / `imageryPromptMode` state and a matching widget row inside the imagery branch. `uploadImage` appends both as FormData fields when `mediaType === 'imagery'`.
2. **Backend** — `routers/ingest.py:upload_imagery` accepts the new `Form(None)` params, validates them (mirroring the FMV resolution block), and when `model=yolo26` rewrites `parsed_enabled_layers` to `["yoloe_pf"]` / `["yoloe_seg"]` and clears or defaults `text_prompts`. The values land in `upload_jobs.metadata` for traceability and are passed through to `process_satellite_imagery.delay(...)`.
3. **Worker** — `process_satellite_imagery` already honored `enabled_layers` end-to-end; no change.
4. **Inference** — `_detect_pipeline` (called by both `/detect` and `/detect_raw`) detects YOLOE-exclusive mode (`_enabled in ({"yoloe_pf"}, {"yoloe_seg"})`) and runs `yoloe.run(bundle["yoloe"], chip3, prompts, threshold)` *instead of* SAM3. The YOLOE bundle was also added to the `imagery` profile's component list so it loads alongside the SAM3 pipeline.

## Why

Two pressures:

1. **Analyst parity** — FMV uploads already had a model/mode dropdown; imagery uploads silently hardwired SAM3. Analysts who wanted a fast prompt-free YOLOE pass on a still ortho (or YOLOE-SEG with a tight class list against a satellite chip) had to hand-craft `enabled_layers` JSON via curl. A first-class UI selector removes that gap.
2. **One mental model** — the cost of exposing two different model-selection idioms (FMV's `model+prompt_mode`, imagery's `enabled_layers`) was both UX confusion and a latent code path (`enabled_layers=["yoloe_*"]`) that *appeared* wired but didn't actually run YOLOE for stills — `_detect_pipeline` had no YOLOE call at all before this change. Investigation revealed `enabled_layers` was an additive filter only (it gated DOTA / GDINO / Prithvi etc., but SAM3 always ran). The exclusive-YOLOE branch added here makes the semantics match the FMV path.

## Considered alternatives

- **Expose YOLOE through `enabled_layers` as an additive layer only.** Rejected — that's what the codebase *appeared* to support but actually didn't, and the additive-vs-exclusive ambiguity is exactly what bit the first round of testing (Pass 2/3/4 all showed `source_layer=sam3` because SAM3 ran regardless of which `enabled_layers` were set). The FMV-shape `(model, prompt_mode)` selector is unambiguous: yolo26 = YOLOE only.
- **Add a third profile (`imagery_yoloe`).** Rejected — profile switching forces a model unload / reload pause. Loading YOLOE into the existing `imagery` profile is cheap (~1 GiB) and lets the user flip between `sam3+pcs` and `yolo26+*` without restart latency.
- **Translate frontend `model=yolo26` into `enabled_layers` server-side without surfacing `model`/`prompt_mode` in the request body.** Rejected — losing the explicit fields breaks API symmetry with `/api/fmv/clips` and makes `upload_jobs.metadata` harder to debug ("which choice did the analyst actually make?").

## Verified end-to-end

On `sample/austin1.tif` (3000×3000 RGB GeoTIFF), each path was uploaded via `POST /api/ingest/upload` and the resulting detections grouped by `source_layer`:

| Upload | `source_layer` | Detections | Notes |
|---|---|---|---|
| `sam3+pcs` (default) | `sam3` | 707 | Existing fusion preserved (building 355 + vehicle 352) |
| `yolo26+amg` | `yoloe` | 20 | LVIS scene labels (`stunning`, `airport_terminal`, `bus`, `viaduct`, …) |
| `yolo26+pcs` + `text_prompts=car,truck,person,aircraft,building` | `yoloe` | 5 | Class `building` — YOLOE honored the prompt list |
| `sam3+amg` | — | HTTP 400 | Rejected with mirror message |
| (no `model`/`prompt_mode` fields) | `sam3` | — | Backward-compat — defaults to `sam3+pcs` |

YOLOE detection counts on imagery are lower than SAM3's because YOLOE-26x at imgsz=640 on chunked aerial chips is more selective; that's expected. The point of the change is *routing*, not relative recall — the cells above prove the right detector runs for each choice.

## Cross-references

- [decisions/why-yoloe-fp32-and-bf16-cast.md](why-yoloe-fp32-and-bf16-cast.md) — prerequisite YOLOE fix (without the bf16 cast, none of the YOLOE paths would emit detections at all)
- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [frontend/workspace-ingest.md](../frontend/workspace-ingest.md)
- [inference/yoloe-tracker.md](../inference/yoloe-tracker.md)
- [inference/profile-pool-lifecycle.md](../inference/profile-pool-lifecycle.md)
- [inference/main-app-entrypoint.md](../inference/main-app-entrypoint.md)
