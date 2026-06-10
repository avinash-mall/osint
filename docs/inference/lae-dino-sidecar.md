# `inference-lae/` — LAE-DINO Open-Vocabulary RS Detector Sidecar

**Path:** [inference-lae/app.py](../../inference-lae/app.py), [inference-lae/Dockerfile](../../inference-lae/Dockerfile)
**Lines:** ~230 (app.py ~230, Dockerfile ~100)
**Depends on:** torch 2.1.0+cu121, mmengine 0.10.4, mmcv 2.1.0 (prebuilt wheel), the LAE-DINO mmdetection fork (`jaychempan/LAE-DINO`, editable), `transformers==4.42.3`, `clip-anytorch` + `open_clip_torch`, `bert-base-uncased`, FastAPI/uvicorn

## Purpose

Standalone GPU microservice that runs **LAE-DINO** (remote-sensing open-vocabulary
text-to-box detector) and exposes it over HTTP. It backs the `grounding_dino`
layer in `inference-sam3`, which calls it via
[grounding_dino.py](../../inference-sam3/grounding_dino.py). Kept separate
because LAE-DINO is a forked mmdetection whose torch/transformers pins conflict
with the SAM 3 stack — see [decisions/why-lae-dino-replaces-grounding-dino.md](../decisions/why-lae-dino-replaces-grounding-dino.md).

## Why this design

A forked-mmdet model can't co-reside with SAM 3 / TerraMind / Prithvi in one
interpreter (transformers 4.42 vs ≥4.56). A sidecar with its own dependency
closure is the only clean boundary. The image pins **torch 2.1.0 + cu121** with
a prebuilt mmcv 2.1.0 wheel — the newest combo mmcv ships wheels for and the
mmdet-3.3 fork is validated against — *not* the host's cu130 profile (no mmcv
wheels) and *not* the fork's 2021 pins. cu121 runs natively on A100/H100 via the
host driver. The service is single-GPU and behind the `lae` compose profile
(opt-in), since the `grounding_dino` layer defaults OFF.

**GPU selection** follows the existing `.env` logic and never hardcodes a card:
`NVIDIA_VISIBLE_DEVICES: ${LAE_VISIBLE_DEVICES:-${SAM3_VISIBLE_DEVICES:-all}}`, so
by default it co-locates on the GEOINT card(s) the operator already gave
inference-sam3 — never a co-tenant's GPUs (e.g. vLLM). Override with
`LAE_VISIBLE_DEVICES`.

## Key symbols

- [`_load`](../../inference-lae/app.py#L72) — builds the mmdet `DetInferencer` once at
  startup. Overrides `language_model.name` → baked BERT dir, nulls the Swin
  `init_cfg`, hoists `cfg.test_pipeline` onto the wrapped ConcatDataset cfg, and
  uses `palette="random"` (not `"none"`) so the missing training-annotation
  dataset is never built. Failures are captured, not raised.
- [`/health`](../../inference-lae/app.py#L133) — `{model_loaded, model, model_error}`; drives the compose healthcheck and the client's `load()` probe.
- [`/detect`](../../inference-lae/app.py#L142) — multipart `file` + JSON `prompts` + `threshold`; runs `DetInferencer(texts="a . b . c", custom_entities=True)` and returns `{detections: [{bbox:[x1,y1,x2,y2], score, label}], model}`. `chunked_size` is left disabled (the fork's chunked predict path is broken; the client chunks prompts instead).
- [`_extract`](../../inference-lae/app.py#L192) — pulls `pred_instances.bboxes/scores/label_names` out of the `DetDataSample`. (A batched `/detect_batch` path was evaluated and removed — see [decisions/why-lae-cross-chip-batching.md](../decisions/why-lae-cross-chip-batching.md).)

## Inputs / Outputs

Input: RGB image bytes + class prompt list. The app converts RGB→BGR for the
mmdet Grounding-DINO `data_preprocessor` (`bgr_to_rgb=True`). Output: xyxy boxes
+ scores + matched entity labels (boxes only — no masks; the client synthesises
bbox-masks and SAM 3 refines downstream).

## Failure modes

- Sidecar absent / model load failed → `/health` reports `model_loaded=false`;
  the inference-sam3 client's `load()` returns `model=None` and the layer
  silently no-ops (graceful, same as a previously-missing dependency).
- `mmcv` built against a mismatched torch → import/runtime error captured in
  `model_error`. Primary build-validation risk (see decision doc).
- Bad prompts/image → empty `detections` with an `error` string.

## Build & run

```bash
python scripts/configure_host.py            # writes SAM3_* GPU build args
docker compose --profile lae up --build inference-lae
```

Weights baked at build: `ML4Sustain/LAE-DINO` checkpoint (MIT, ~725 MB) +
`google-bert/bert-base-uncased`. `HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE=1` at
runtime.

## Cross-references

- [inference/grounding-dino-detector.md](grounding-dino-detector.md) — the client
- [inference/grounding-dino-gate.md](grounding-dino-gate.md) — when the layer runs
- [decisions/why-lae-dino-replaces-grounding-dino.md](../decisions/why-lae-dino-replaces-grounding-dino.md)
- [deployment/docker-compose-services.md](../deployment/docker-compose-services.md)
- [architecture/service-topology.md](../architecture/service-topology.md)
