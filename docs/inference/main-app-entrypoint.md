# `inference-sam3/main.py` — Service Entrypoint

**Path:** [inference-sam3/main.py](../../inference-sam3/main.py)
**Lines:** ~1660
**Depends on:** Every other module in `inference-sam3/`, plus `torch`, `cv2`, `PIL`, `fastapi`

## Purpose

FastAPI app for the GPU inference service. Holds the request handlers (`/health`, `/load`, `/unload`, `/detect`, `/detect_video`), the profile pool lifecycle, per-request metrics, and the prompt-resolution cache.

## Why this design

`/detect` is precision-first by default: explicit `metadata.text_prompts` always win, explicit empty `text_prompts` is a 400, and omitted prompts use a small sensor-specific default set unless `SAM3_DEFAULT_PROMPT_SOURCE=ontology` opts back into the backend ontology fan-out. Candidate detections can optionally receive RemoteCLIP verifier metadata, but the verifier never proposes detections. See [why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md) and [why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md).

## Key symbols (request handlers)

- [`health`](../../inference-sam3/main.py#L731-L766) — `GET /health`
- [`memory_health`](../../inference-sam3/main.py#L771-L799) — `GET /health/memory`
- [`memory_reset`](../../inference-sam3/main.py#L802-L814) — `POST /health/memory/reset`
- [`load_profile`](../../inference-sam3/main.py#L817-L820) — `POST /load`
- [`unload_models`](../../inference-sam3/main.py#L824-L835) — `POST /unload` (re-execs the process)
- `/detect` handler — [#L1095-L1173](../../inference-sam3/main.py#L1095-L1173)
- `/detect_video` handler — [#L1288-L1557](../../inference-sam3/main.py#L1288-L1557)

## Key symbols (internal)

- [`lifespan`](../../inference-sam3/main.py#L120) — async contextmanager passed to `FastAPI(...)` that runs `preload_models_on_startup()` on boot. Replaces the deprecated `@app.on_event("startup")` pattern.
- `_track` — context-manager that records per-stage timings into `/health`.
- [`resolve_prompts`](../../inference-sam3/main.py#L421-L448) — prompt resolution ladder.
- [`_prompts_relevant_to_dota`](../../inference-sam3/main.py#L476-L480) — gate input for DOTA-OBB.
- [`_tag_candidates`](../../inference-sam3/main.py#L481-L483) — carries per-layer provenance (`source_layer`) through fusion.
- [`_build_component`](../../inference-sam3/main.py#L503-L524), [`_load_profile`](../../inference-sam3/main.py#L563-L596), [`_ensure_profile`](../../inference-sam3/main.py#L599-L613) — profile pool lifecycle.
- [`_version_snapshot`](../../inference-sam3/main.py#L723-L727) — combines SAM3, OBB, and verifier version metadata.

## /detect request contract

| Field | Default | Description |
|---|---|---|
| `image` (multipart) | required | PNG (RGB chip) or GeoTIFF (multispectral / SAR) |
| `metadata.modality` | `"rgb"` | `rgb` / `multispectral` / `sar` |
| `metadata.text_prompts` | precision defaults | List of strings to text-prompt SAM3. Explicit empty list is a 400 unless `prompt_boxes` are supplied |
| `metadata.prompt_boxes` | `[]` | Box prompts: `[{bbox: cxcywh_norm, class: str}, ...]` |
| `metadata.enabled_layers` | profile defaults | Subset of `sam3, dota_obb, grounding_dino, remoteclip, dinov3_sat, prithvi, terramind, yoloe` |
| `metadata.hls_timesteps` | `1` | Set to `3` for HLS multi-temporal crop classifier |

Per-detection output includes `source_layer` (`sam3`, `dota_obb`, `grounding_dino`, etc.) so backend calibration and NMS provenance can distinguish detector families. When `remoteclip` is loaded and enabled, detections also include `semantic_verifier` and `semantic_margin` for backend evidence ranking. The response includes `debug_counts` (`prompt_count`, `candidates_by_layer`, `suppressed_by_nms`, `suppressed_by_policy`) for false-positive triage.

## /detect_video request contract

| Field | Default | Description |
|---|---|---|
| `video` (multipart) | required | MP4 or path via `metadata.video_path` |
| `metadata.prompt_mode` | `"pcs"` | `pcs` (SAM 3.1 multiplex) or `yoloe` |
| `metadata.text_prompts` | caller / worker defaults | When mode=yoloe and empty → `-pf` head |
| `metadata.frame_stride` | `1` | Process every Nth frame |

Streams `application/x-ndjson`, one record per frame × track.

## Failure modes

- Invalid JSON metadata is treated as `{}` for `/detect`; missing prompts then use precision defaults.
- Explicit empty `metadata.text_prompts` returns 400 in image detection to prevent accidental broad fallback.
- `SAM3_DEFAULT_PROMPT_SOURCE=ontology` restores backend default prompts; if the backend is unreachable, callers receive 503.
- Specialist layers are skipped rather than failed when relevance gates do not pass; forced flags (`force_dota_obb`, `force_grounding_dino`) override that for experiments.

## Cross-references

- [service-overview.md](service-overview.md)
- [sam3-runner-internals.md](sam3-runner-internals.md)
- [profile-pool-lifecycle.md](profile-pool-lifecycle.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [decisions/why-category-presence-gate.md](../decisions/why-category-presence-gate.md)
- [decisions/why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md)
