# Ingest Router (`/api/ingest/*`)

**Path:** [backend/routers/ingest.py](../../backend/routers/ingest.py)
**Lines:** ~584 (the largest router)
**Depends on:** [backend/files.py](../../backend/files.py), [backend/fmv_helpers.py](../../backend/fmv_helpers.py), [backend/imagery_metadata.py](../../backend/imagery_metadata.py), [backend/video_metadata.py](../../backend/video_metadata.py), [backend/worker/](../../backend/worker/), [backend/ontology.py](../../backend/ontology.py), [backend/provider_lifecycle.py](../../backend/provider_lifecycle.py)

Router declared with `prefix="/api/ingest"` — endpoints below relative to that.

## Purpose

Three ways to push data in: a URL, a disk path, or a direct upload. Imagery → `worker.process_satellite_imagery`; FMV → `worker.process_fmv` after HLS transcode.

## Endpoints

| Method | Path | Full path | Source | Behavior |
|---|---|---|---|---|
| `GET` | `/uploads` | `/api/ingest/uploads` | [ingest.py#L163](../../backend/routers/ingest.py#L163) | List recent upload rows |
| `GET` | `/jobs/{task_id}` | `/api/ingest/jobs/{task_id}` | [ingest.py#L181](../../backend/routers/ingest.py#L181) | Celery task status |
| `POST` | `""` | `/api/ingest` | [ingest.py#L204](../../backend/routers/ingest.py#L204) | `IngestRequest` — path or URL already on the local shared volume |
| `POST` | `/upload` | `/api/ingest/upload` | [ingest.py#L220](../../backend/routers/ingest.py#L220) | Multipart upload — imagery or FMV; classified by extension via [files.classify_upload](../../backend/files.py). Optional form fields: `text_prompts`, `ontology_branch` (scopes ontology-mode detection vocabulary to one branch), `modality`, `enabled_layers` |
| `POST` | `/url` | `/api/ingest/url` | [ingest.py#L547](../../backend/routers/ingest.py#L547) | `IngestUrlRequest` — backend downloads from a remote URL |

`POST /api/fmv/clips` (FMV-specific upload entry) is **also** in this file though its path isn't under `/api/ingest` — shares the transcode/telemetry/Celery-dispatch code.

## Why this design

- **Three entry points** — heterogeneous data sources: operator with a file, automated upstream pushing URLs, workflow that already staged a file on the shared volume.
- **Sensor selection drives `modality` + `enabled_layers`** in the body — see [architecture/data-flow-imagery.md#modality-dispatch](../architecture/data-flow-imagery.md#modality-dispatch).
- **HLS transcode before Celery dispatch** → client streams the clip while detection runs. Worker emits `fmv_detections_complete` over WebSocket on finish.
- **Upload job rows written synchronously** → UI progress bar populates before the Celery task starts.

## Failure modes

- Unrecognized media type → 415.
- Malformed `IngestUrlRequest` URL → 400.
- Provider unavailable (inference-sam3 not loaded) → request still accepted; worker queues internally and retries — see [backend/provider-lifecycle.md](../backend/provider-lifecycle.md).
- Disk full on shared volume → 507.

## Cross-references

- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [backend/files-and-uploads.md](../backend/files-and-uploads.md)
- [backend/fmv-helpers-hls.md](../backend/fmv-helpers-hls.md)
- [operations/imagery-ingest-pipeline.md](../operations/imagery-ingest-pipeline.md)
- [frontend/workspace-ingest.md](../frontend/workspace-ingest.md)
