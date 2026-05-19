# `backend/files.py` — Upload Helpers

**Path:** [backend/files.py](../../backend/files.py)
**Lines:** ~52
**Depends on:** `pathlib`, FastAPI's `UploadFile`

## Purpose

Three small primitives shared between the ingest and models-training routers: filename sanitization, streamed upload-to-disk, and a media-type classifier that maps extension → `(media_type, celery_task_name)`.

## Why this design

- **Streamed write** (1 MiB chunks) so large uploads don't blow process RAM.
- **Sanitize filenames defensively.** Avoids path-traversal (`../`) and odd Unicode. The original filename is preserved in the database row for the UI.
- **Classification drives Celery routing.** A `.tif` uploaded through `/api/ingest/upload` becomes `worker.process_satellite_imagery`; a `.mp4` becomes `worker.process_fmv`. Single source of truth for the extension → task map.

## Key symbols

- [`safe_filename`](../../backend/files.py#L11).
- [`save_upload_file`](../../backend/files.py#L17) — returns bytes written.
- [`classify_upload`](../../backend/files.py#L33) — `(media_type, celery_task_name)`.

## Cross-references

- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [backend-routers/models-training-router.md](../backend-routers/models-training-router.md)
