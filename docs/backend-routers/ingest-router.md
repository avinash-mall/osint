# Ingest Router (`/api/ingest/*`)

**Path:** [backend/routers/ingest.py](../../backend/routers/ingest.py)
**Lines:** ~712 (the largest router)
**Depends on:** [backend/files.py](../../backend/files.py), [backend/fmv_helpers.py](../../backend/fmv_helpers.py), [backend/imagery_metadata.py](../../backend/imagery_metadata.py), [backend/video_metadata.py](../../backend/video_metadata.py), [backend/worker/](../../backend/worker/), [backend/provider_lifecycle.py](../../backend/provider_lifecycle.py)

Router declared with `prefix="/api/ingest"` — endpoints below relative to that.

## Purpose

Three ways to push data in: a URL, a disk path, or a direct upload. Imagery → `worker.process_satellite_imagery`; FMV → `worker.process_fmv` after HLS transcode.

## Endpoints

| Method | Path | Full path | Source | Behavior |
|---|---|---|---|---|
| `GET` | `/uploads` | `/api/ingest/uploads` | [ingest.py#L177](../../backend/routers/ingest.py#L177) | List recent upload rows |
| `GET` | `/jobs/{task_id}` | `/api/ingest/jobs/{task_id}` | [ingest.py#L195](../../backend/routers/ingest.py#L195) | Celery task status |
| `POST` | `""` | `/api/ingest` | [ingest.py#L216](../../backend/routers/ingest.py#L216) | `IngestRequest` — path or URL already on the local shared volume |
| `POST` | `/upload` | `/api/ingest/upload` | [ingest.py#L282](../../backend/routers/ingest.py#L282) | Multipart upload — imagery or FMV; classified by extension via [files.classify_upload](../../backend/files.py). Imagery accepts `text_prompts`, `ontology_branch`, `modality`, and `enabled_layers` for the SAM3 sensor pipeline; `model=yolo26` and YOLOE layers are rejected because YOLOE is FMV-only. FMV still accepts `model` (`sam3`/`yolo26`) and `prompt_mode` (`pcs`/`amg`): `model=yolo26` maps to worker mode `yoloe`; FMV `amg` clears prompts, while PCS parses `text_prompts` JSON/comma lists or falls back to bounded defaults. See [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md). |
| `POST` | `/url` | `/api/ingest/url` | [ingest.py#L628](../../backend/routers/ingest.py#L628) | `IngestUrlRequest` — backend downloads from a remote URL |

`POST /api/fmv/clips` (FMV-specific upload entry) lives in [backend/main.py](../backend/main-app-entrypoint.md); `/api/ingest/upload` keeps its own generic FMV branch for uploads submitted through the Ingest workspace.

## Why this design

- **Three entry points** — heterogeneous data sources: operator with a file, automated upstream pushing URLs, workflow that already staged a file on the shared volume.
- **Sensor selection drives `modality` + `enabled_layers`** in the body — see [architecture/data-flow-imagery.md#modality-dispatch](../architecture/data-flow-imagery.md#modality-dispatch).
- **HLS transcode before Celery dispatch** → client streams the clip while detection runs. Worker emits `fmv_detections_complete` over WebSocket on finish.
- **Upload job rows written synchronously** → UI progress bar populates before the Celery task starts.
- **Generic FMV honors the operator's mode/model choice** → it validates `model` + `prompt_mode` before staging work and queues the same PCS/YOLOE worker modes used by `/api/fmv/clips`. Imagery deliberately rejects YOLOE and stays on the SAM3 sensor pipeline.

## Failure modes

- Unrecognized media type → 415.
- Malformed `IngestUrlRequest` URL → 400.
- Provider unavailable (inference-sam3 not loaded) → request still accepted; worker queues internally and retries — see [backend/provider-lifecycle.md](../backend/provider-lifecycle.md).
- Disk full on shared volume → 507.
- FMV telemetry missing/malformed → 422 and the staged clip/HLS directory is removed.
- Imagery `model=yolo26` or `enabled_layers` containing `yoloe_pf` / `yoloe_seg` → 400; YOLOE is reserved for FMV.

## Cross-references

- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [backend/files-and-uploads.md](../backend/files-and-uploads.md)
- [backend/fmv-helpers-hls.md](../backend/fmv-helpers-hls.md)
- [operations/imagery-ingest-pipeline.md](../operations/imagery-ingest-pipeline.md)
- [frontend/workspace-ingest.md](../frontend/workspace-ingest.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
