# Models & Training Router (`/api/models/*`, `/api/training/*`)

**Path:** [backend/routers/models_training.py](../../backend/routers/models_training.py)
**Lines:** ~153
**Depends on:** [backend/files.py](../../backend/files.py), [backend/events.py](../../backend/events.py), [backend/schemas.py](../../backend/schemas.py)

## Purpose

Registered detection models, curated training datasets, and the training-job queue surfaced in **Admin → AI models** and **Admin → Processing**.

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET` | `/api/models/datasets` | [models_training.py#L26](../../backend/routers/models_training.py#L26) | List registered training datasets |
| `GET` | `/api/models` | [models_training.py#L38](../../backend/routers/models_training.py#L38) | List registered detection models |
| `POST` | `/api/models/datasets` | [models_training.py#L51](../../backend/routers/models_training.py#L51) | Register a dataset (multipart) |
| `POST` | `/api/models/{model_id}/promote` | [models_training.py#L85](../../backend/routers/models_training.py#L85) | Promote a model to "active" |
| `POST` | `/api/training/jobs` | [models_training.py#L105](../../backend/routers/models_training.py#L105) | Queue a training job via [`TrainingJobCreate`](../../backend/schemas.py) |
| `GET` | `/api/training/jobs` | [models_training.py#L144](../../backend/routers/models_training.py#L144) | Job log |

## Why this design

- **Registry, not training infra.** The router stores metadata and forwards training requests to the inference service (which has the GPU). The backend doesn't have CUDA.
- **Promotion is operator-driven.** Even when training reports better metrics, switching the active model is a single human-in-the-loop action: `POST /api/models/{id}/promote` writes a row in `model_history` and updates a single active-model pointer.

## Cross-references

- [backend/files-and-uploads.md](../backend/files-and-uploads.md)
- [scripts/backend-scripts-train-and-seed.md](../scripts/backend-scripts-train-and-seed.md) — the actual training script
- [frontend/admin-models-and-processing.md](../frontend/admin-models-and-processing.md)
