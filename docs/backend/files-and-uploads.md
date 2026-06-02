# `backend/files.py` — Upload Helpers

**Path:** [backend/files.py](../../backend/files.py)
**Lines:** ~69
**Depends on:** `os`, `pathlib`, FastAPI `HTTPException` + `UploadFile`, env `MAX_UPLOAD_BYTES`

## Purpose

Shared upload primitives for ingest and model-training routers: filename sanitization, bounded streamed upload-to-disk, and media-type classification.

## Why this design

- **Streamed write** (1 MiB chunks) — large uploads don't blow process RAM.
- **Byte ceiling at the shared helper** — every upload path gets the same `MAX_UPLOAD_BYTES` enforcement and cleanup-on-413 behavior.
- **Sanitize filenames defensively** — avoids path-traversal (`../`) and odd Unicode. Original filename preserved in the DB row for the UI.
- **Classification drives Celery routing** — `.tif` via `/api/ingest/upload` → `worker.process_satellite_imagery`; `.mp4` → `worker.process_fmv`. Single source of truth for the extension → task map.

## Key symbols

- [`safe_filename`](../../backend/files.py#L14-L17) — strips path components and unsafe characters.
- [`max_upload_bytes`](../../backend/files.py#L20-L25) — reads the upload byte ceiling from `MAX_UPLOAD_BYTES`.
- [`save_upload_file`](../../backend/files.py#L28-L47) — streams to disk, raises 413 on over-limit uploads, and removes partial files.
- [`classify_upload`](../../backend/files.py#L50-L69) — `(media_type, celery_task_name)`. Video suffixes: `.mp4 .mov .m4v .ts .mpeg .mpg` (note `.mpg/.mpeg` MPEG-TS drone feeds **are** accepted; `.mkv` is **not**). The frontend file-picker `accept` lists (`FMV_FILE_ACCEPT` / `IMAGERY_FILE_ACCEPT` in `IngestConnect.tsx`) must mirror this set, or the native picker filters out files the backend would happily ingest (this drifted: the UI listed `.mkv` and omitted `.mpg`).

## Inputs / Outputs

`save_upload_file(file, local_path)` consumes a FastAPI `UploadFile` and destination `Path`; it returns the total bytes written. `classify_upload(filename)` returns `(media_type, handler)` or raises HTTP 400 for unsupported suffixes.

## Failure modes

Over-limit uploads raise HTTP 413 and delete the partially written file. Unsupported suffixes raise HTTP 400. Invalid `MAX_UPLOAD_BYTES` falls back to the 10 GiB default; setting it to `0` or a negative value disables the cap.

## Cross-references

- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [backend-routers/models-training-router.md](../backend-routers/models-training-router.md)
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
- [decisions/why-security-hardening-2026-05-31.md](../decisions/why-security-hardening-2026-05-31.md)
