# `backend/files.py` — Upload Helpers

**Path:** [backend/files.py](../../backend/files.py)
**Lines:** ~52
**Depends on:** `pathlib`, FastAPI `UploadFile`

## Purpose

Three primitives shared between ingest and models-training routers: filename sanitization, streamed upload-to-disk, media-type classifier (extension → `(media_type, celery_task_name)`).

## Why this design

- **Streamed write** (1 MiB chunks) — large uploads don't blow process RAM.
- **Sanitize filenames defensively** — avoids path-traversal (`../`) and odd Unicode. Original filename preserved in the DB row for the UI.
- **Classification drives Celery routing** — `.tif` via `/api/ingest/upload` → `worker.process_satellite_imagery`; `.mp4` → `worker.process_fmv`. Single source of truth for the extension → task map.

## Key symbols

- [`safe_filename`](../../backend/files.py#L11).
- [`save_upload_file`](../../backend/files.py#L17) — returns bytes written.
- [`classify_upload`](../../backend/files.py#L33) — `(media_type, celery_task_name)`.

## Cross-references

- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [backend-routers/models-training-router.md](../backend-routers/models-training-router.md)
