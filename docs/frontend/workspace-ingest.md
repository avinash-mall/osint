# Ingest Workspace — `IngestConnect.tsx`

**Path:** [frontend/src/components/IngestConnect.tsx](../../frontend/src/components/IngestConnect.tsx)
**Lines:** ~39083 characters (~1000 lines TSX)

## Purpose

Single entry point for getting data into the platform. Combines sensor-aware imagery upload, FMV clip upload, URL ingest, feed lifecycle management.

## Sections

1. **Imagery upload** — sensor dropdown (Optical / Multispectral / Hyperspectral / SAR / FMV) drives `modality` + `enabled_layers` for the request body — see [architecture/data-flow-imagery.md#modality-dispatch](../architecture/data-flow-imagery.md#modality-dispatch).
2. **FMV upload** — multi-file MP4 + optional `.srt` sidecar.
3. **URL ingest** — fetches from a remote URL on the backend side.
4. **Feeds** — connect/disconnect HTTP polling feeds; per-feed event log.
5. **Recent uploads** — live `GET /api/ingest/uploads` listing with per-row status and a link to the resulting satellite pass / FMV clip.

## Data sources

- `POST /api/ingest/upload`, `/api/ingest`, `/api/ingest/url`
- `POST /api/fmv/clips` (FMV-specific upload entry)
- `GET /api/ingest/uploads`
- `GET /api/ingest/jobs/{task_id}` (status polling)
- `POST /api/feeds/connect` + related
- WebSocket: `ingest_progress` for live progress

## Cross-references

- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [operations/imagery-ingest-pipeline.md](../operations/imagery-ingest-pipeline.md)
