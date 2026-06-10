# `inference-lae/` — LAE-DINO Open-Vocabulary RS Detector Sidecar

**Path:** [inference-lae/app.py](../../inference-lae/app.py), [inference-lae/Dockerfile](../../inference-lae/Dockerfile)
**Lines:** ~210 (app.py ~210, Dockerfile ~80)
**Depends on:** torch (host GPU profile), mmengine 0.10.4, mmcv 2.0–2.2 (built from source), LAE-DINO mmdetection fork (`jaychempan/LAE-DINO`), `bert-base-uncased`, FastAPI/uvicorn

## Purpose

Standalone GPU microservice that runs **LAE-DINO** (remote-sensing open-vocabulary
text-to-box detector) and exposes it over HTTP. It backs the `grounding_dino`
layer in `inference-sam3`, which calls it via
[grounding_dino.py](../../inference-sam3/grounding_dino.py). Kept separate
because LAE-DINO is a forked mmdetection whose torch/transformers pins conflict
with the SAM 3 stack — see [decisions/why-lae-dino-replaces-grounding-dino.md](../decisions/why-lae-dino-replaces-grounding-dino.md).

## Why this design

A forked-mmdet model can't co-reside with SAM 3 / TerraMind / Prithvi in one
interpreter (torch 1.10 vs 2.x; transformers 4.42 vs ≥4.56). A sidecar with its
own dependency closure is the only clean boundary. The image is built against
the **same** CUDA/torch the host GPU profile selects (`SAM3_*` build args from
`scripts/configure_host.py`) so it runs on the same modern GPUs as the rest of
the stack — LAE-DINO's 2021 upstream pins are deliberately *not* used. The
service is single-GPU and behind the `lae` compose profile (opt-in), since the
`grounding_dino` layer defaults OFF.

## Key symbols

- [`_load`](../../inference-lae/app.py#L70) — builds the mmdet `DetInferencer` once at
  startup; overrides `language_model.name` → baked BERT dir and nulls the Swin
  `init_cfg` so nothing is fetched at runtime. Failures are captured, not raised.
- [`/health`](../../inference-lae/app.py#L116) — `{model_loaded, model, model_error}`; drives the compose healthcheck and the client's `load()` probe.
- [`/detect`](../../inference-lae/app.py#L125) — multipart `file` + JSON `prompts` + `threshold`; runs `DetInferencer(texts="a . b . c", custom_entities=True)` and returns `{detections: [{bbox:[x1,y1,x2,y2], score, label}], model}`.
- [`_extract`](../../inference-lae/app.py#L175) — pulls `pred_instances.bboxes/scores/label_names` out of the `DetDataSample`.

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
