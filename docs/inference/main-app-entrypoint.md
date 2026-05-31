# `inference-sam3/main.py` — Service Entrypoint

**Path:** [inference-sam3/main.py](../../inference-sam3/main.py)
**Lines:** ~1740
**Depends on:** Every other module in `inference-sam3/`, plus `torch`, `cv2`, `PIL`, `fastapi`

## Purpose

FastAPI app for the GPU inference service. Holds request handlers (`/health`, `/load`, `/unload`, `/detect`, `/embed`, `/detect_video`), the profile pool lifecycle, per-request metrics, the prompt-resolution cache.

## Why this design

`/detect` is precision-first by default: explicit `metadata.text_prompts` always win; explicit empty `text_prompts` → 400; omitted prompts → small sensor-specific default set unless `SAM3_DEFAULT_PROMPT_SOURCE=ontology` opts back into the backend ontology fan-out. In that ontology fan-out, `metadata.ontology_branch` scopes the vocabulary to one branch + descendants — a smaller, scene-relevant prompt set that is the primary lever against open-vocabulary false positives. Omitted prompts now resolve to a richer common-target precision set (building/vehicle/car/aircraft/ship/road/…) so a no-prompt upload still detects the usual GEOINT objects. See [why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md), [why-deconflicted-detection-prompts.md](../decisions/why-deconflicted-detection-prompts.md).

## Key symbols (request handlers)

- [`health`](../../inference-sam3/main.py#L776-L813) — `GET /health`
- [`memory_health`](../../inference-sam3/main.py#L817-L848) — `GET /health/memory`
- [`memory_reset`](../../inference-sam3/main.py#L852-L862) — `POST /health/memory/reset`
- [`load_profile`](../../inference-sam3/main.py#L866-L875) — `POST /load`
- [`unload_models`](../../inference-sam3/main.py#L879-L900) — `POST /unload` (re-execs the process)
- `/detect` handler — [#L1140-L1219](../../inference-sam3/main.py#L1140-L1219)
- [`embed_endpoint`](../../inference-sam3/main.py#L1223-L1251) — `POST /embed` — standalone DINOv3-SAT 1024-d embedding for bake scripts + analyst lookup; auto-loads the imagery profile on first call; returns `{model, dim, fp16_b64}`. 503 when the active profile lacks `dinov3_sat`.
- `/detect_raw` handler — [#L1255-L1359](../../inference-sam3/main.py#L1255-L1359)
- `/detect_video` handler — [#L1367-L1552](../../inference-sam3/main.py#L1367-L1552)

## Key symbols (internal)

- [`lifespan`](../../inference-sam3/main.py#L123) — async contextmanager passed to `FastAPI(...)`, runs `preload_models_on_startup()` on boot, then ensures `SAM3_RESTING_PROFILE` (default `imagery`; `imagery_rgb` on dynamic-VRAM cards) is resident for the healthcheck. Replaces deprecated `@app.on_event("startup")`.
- `_track` — context-manager recording per-stage timings into `/health`.
- [`resolve_prompts`](../../inference-sam3/main.py#L460-L508) — prompt resolution ladder.
- [`_reject_image_yoloe_layers`](../../inference-sam3/main.py#L277-L283) — rejects image `/detect` requests that try to enable FMV-only YOLOE layers.
- [`_prompts_relevant_to_dota`](../../inference-sam3/main.py#L521-L523) — gate input for DOTA-OBB.
- [`_tag_candidates`](../../inference-sam3/main.py#L526-L527) — carries per-layer provenance (`source_layer`) through fusion.
- [`_build_component`](../../inference-sam3/main.py#L602-L612), [`_load_profile`](../../inference-sam3/main.py#L665-L699), [`_ensure_profile`](../../inference-sam3/main.py#L702-L728) — profile pool lifecycle. `_ensure_profile` short-circuits when a resident superset (`imagery` union or `all`) already covers the requested profile's components, so hot cards never reload.
- [`_profile_for_modality`](../../inference-sam3/main.py#L730-L744) — maps a `/detect` request `modality` (`rgb`/`multispectral`/`sar`) to its per-modality imagery profile (`imagery_rgb`/`imagery_msi`/`imagery_sar`); `/detect` and `/detect_raw` route through it so tight-VRAM cards hold one modality's models at a time. See [decisions/why-dynamic-modality-loading-on-tight-vram.md](../decisions/why-dynamic-modality-loading-on-tight-vram.md).
- [`_version_snapshot`](../../inference-sam3/main.py#L768-L772) — combines SAM3, OBB, verifier version metadata.

## /detect request contract

| Field | Default | Description |
|---|---|---|
| `image` (multipart) | required | PNG (RGB chip) or GeoTIFF (multispectral / SAR) |
| `metadata.modality` | `"rgb"` | `rgb` / `multispectral` / `sar` |
| `metadata.text_prompts` | precision defaults | Strings to text-prompt SAM3. Explicit empty list → 400 unless `prompt_boxes` supplied |
| `metadata.ontology_branch` | none (full vocab) | Ontology branch id; in `SAM3_DEFAULT_PROMPT_SOURCE=ontology` mode, scopes the fetched vocabulary to that branch + descendants. Ignored when `text_prompts` is given |
| `metadata.prompt_boxes` | `[]` | Box prompts: `[{bbox: cxcywh_norm, class: str}, ...]` |
| `metadata.enabled_layers` | profile defaults | Subset of `sam3, dota_obb, grounding_dino, dinov3_sat, prithvi, terramind`. `yoloe`, `yoloe_pf`, and `yoloe_seg` are rejected on image endpoints because YOLOE is FMV-only. See [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md). |
| `metadata.hls_timesteps` | `1` | Set `3` for HLS multi-temporal crop classifier |

Per-detection output includes `source_layer` (`sam3`, `dota_obb`, `grounding_dino`, etc.) → backend calibration + NMS provenance distinguish detector families. Response includes `debug_counts` (`prompt_count`, `candidates_by_layer`, `suppressed_by_nms`, `suppressed_by_policy`) for false-positive triage. (The RemoteCLIP verifier that previously emitted `semantic_verifier`/`semantic_margin` was removed — see [decisions/removed-fair1m-and-remoteclip.md](../decisions/removed-fair1m-and-remoteclip.md).)

## /detect_video request contract

| Field | Default | Description |
|---|---|---|
| `video` (multipart) | required | MP4 or path via `metadata.video_path` |
| `metadata.prompt_mode` | `"pcs"` | `pcs` (SAM 3.1 multiplex) or `yoloe` |
| `metadata.text_prompts` | caller / worker defaults | mode=yoloe and empty → `-pf` head |
| `metadata.frame_stride` | `1` | Process every Nth frame |

Streams `application/x-ndjson`, one record per frame × track.

## Failure modes

- **Profile-swap race → 503, not a crash.** `/detect` auto-heals to the imagery profile, then guards `bundle.get("sam3_image") is None` before running prompts: a concurrent FMV `/load` can swap the pool to `fmv` (no `sam3_image`) between the ensure and the run, which used to crash with `TypeError: 'NoneType' object is not subscriptable`. Now it raises 503 (retryable backpressure). `run_text_prompts`/`run_box_prompts` carry the same guard. See [decisions/why-503-on-unloaded-component.md](../decisions/why-503-on-unloaded-component.md).
- Invalid JSON metadata treated as `{}` for `/detect`; missing prompts then use precision defaults.
- Image `/detect` and `/detect_raw` with `enabled_layers` containing `yoloe`, `yoloe_pf`, or `yoloe_seg` → 400; use `/detect_video` for YOLOE FMV tracking.
- Explicit empty `metadata.text_prompts` → 400 in image detection (prevents accidental broad fallback).
- `SAM3_DEFAULT_PROMPT_SOURCE=ontology` restores backend default prompts; backend unreachable → callers get 503.
- Specialist layers skipped (not failed) when relevance gates don't pass; forced flags (`force_dota_obb`, `force_grounding_dino`) override for experiments.

## Cross-references

- [service-overview.md](service-overview.md)
- [sam3-runner-internals.md](sam3-runner-internals.md)
- [profile-pool-lifecycle.md](profile-pool-lifecycle.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [decisions/why-category-presence-gate.md](../decisions/why-category-presence-gate.md)
- [decisions/why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
