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

## Failure modes

- Missing/expired session → 401; non-admin session → 403.
- Invalid upload payloads or nonexistent model ids return 4xx without changing registry state.

## Cross-references

- [backend/files-and-uploads.md](../backend/files-and-uploads.md)
- [scripts/backend-scripts-train-and-seed.md](../scripts/backend-scripts-train-and-seed.md) — the actual training script
- [frontend/admin-models-and-processing.md](../frontend/admin-models-and-processing.md)
- [decisions/why-admin-mutators-require-admin.md](../decisions/why-admin-mutators-require-admin.md)
