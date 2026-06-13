# Models & Training Router (`/api/models/*`, `/api/training/*`)

**Path:** [backend/routers/models_training.py](../../backend/routers/models_training.py)
**Lines:** ~155
**Depends on:** [backend/auth.py](../../backend/auth.py) (`require_admin`), [backend/files.py](../../backend/files.py), [backend/events.py](../../backend/events.py), [backend/schemas.py](../../backend/schemas.py)

## Purpose

Registered detection models, curated training datasets, training-job queue — surfaced in **Admin → AI models** and **Admin → Processing**.

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET` | `/api/models/datasets` | [models_training.py#L28](../../backend/routers/models_training.py#L28) | List registered training datasets; admin session required |
| `GET` | `/api/models` | [models_training.py#L40](../../backend/routers/models_training.py#L40) | List registered detection models; admin session required |
| `POST` | `/api/models/datasets` | [models_training.py#L53](../../backend/routers/models_training.py#L53) | Register a dataset (multipart); admin session required |
| `POST` | `/api/models/{model_id}/promote` | [models_training.py#L88](../../backend/routers/models_training.py#L88) | Promote a model to "active"; admin session required |
| `POST` | `/api/training/jobs` | [models_training.py#L108](../../backend/routers/models_training.py#L108) | Queue a training job via [`TrainingJobCreate`](../../backend/schemas.py); admin session required |
| `GET` | `/api/training/jobs` | [models_training.py#L147](../../backend/routers/models_training.py#L147) | Job log; admin session required |

## Why this design

- **Registry, not training infra** — router stores metadata, forwards training requests to the inference service (which has the GPU). Backend has no CUDA.
- **Promotion admin-driven** — even when training reports better metrics, switching the active model is a single human-in-the-loop action: `POST /api/models/{id}/promote` writes a `model_history` row, updates a single active-model pointer.
- **Admin role required** because model promotion and queued training jobs change global inference behavior and GPU load.

## Training pipeline (worker → scripts/train.py → inference /train)

`POST /api/training/jobs` enqueues `worker.train_model` ([worker_legacy.py#L5715](../../backend/worker_legacy.py#L5715)), which shells out to [`scripts/train.py`](../../backend/scripts/train.py); that script POSTs to inference-sam3 `/train`, polls `/train/{job_id}`, and on success registers a `models` row.

- **Opt-in chip-aligned tiling.** When the queued job's `metrics.tile` is truthy, `train_model` ([worker_legacy.py#L5759](../../backend/worker_legacy.py#L5759)) passes `--tile` (plus optional `--chip-size` / `--overlap`) to `train.py`. `train.py._maybe_tile_dataset` ([scripts/train.py#L75](../../backend/scripts/train.py#L75)) then runs [`prepare_training_tiles.tile_dataset`](../../backend/scripts/prepare_training_tiles.py) to cut training tiles with the SAME `plan_inference_grid` planner the inference worker uses, so train/inference pixel distributions match. Default-safe: absent the flag, behaviour is unchanged. Images already ≤ chip_size (e.g. MVRSD's 640 px chips) pass straight through as a single tile. See [chip-aligned-training-tiles.md](../decisions/chip-aligned-training-tiles.md).
- **Optional device pin.** The inference `/train` body now accepts an optional `device` field ([inference-sam3/main.py#L2012](../../inference-sam3/main.py#L2012)) forwarded into `model.train(device=...)` ([inference-sam3/main.py#L1987](../../inference-sam3/main.py#L1987)). On a host whose cuda:0/1 are saturated by live inference replicas, pinning a fine-tune to a free card (e.g. `"2"`) keeps it from OOM-crashing the serving process. Absent → ultralytics' default (cuda:0). NOTE: a running container built before this change ignores the field; rebuild the inference image to use it.

## Failure modes

- Missing/expired session → 401; non-admin session → 403.
- Invalid upload payloads or nonexistent model ids return 4xx without changing registry state.

## Cross-references

- [backend/files-and-uploads.md](../backend/files-and-uploads.md)
- [scripts/backend-scripts-train-and-seed.md](../scripts/backend-scripts-train-and-seed.md) — the actual training script
- [frontend/admin-models-and-processing.md](../frontend/admin-models-and-processing.md)
- [decisions/why-admin-mutators-require-admin.md](../decisions/why-admin-mutators-require-admin.md)
