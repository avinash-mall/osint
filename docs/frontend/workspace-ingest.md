# Ingest Workspace — `IngestConnect.tsx`

**Path:** [frontend/src/components/IngestConnect.tsx](../../frontend/src/components/IngestConnect.tsx)
**Lines:** ~1150

## Purpose

Single entry point for getting data into the platform. Combines sensor-aware imagery upload, FMV clip upload, URL ingest, feed lifecycle management.

## Sections

1. **Imagery upload** — sensor dropdown (Optical / Multispectral / Hyperspectral / SAR / FMV) drives `modality` + `enabled_layers` for the SAM3 sensor pipeline — see [architecture/data-flow-imagery.md#modality-dispatch](../architecture/data-flow-imagery.md#modality-dispatch). YOLOE is not exposed for still imagery.
2. **Vocabulary scope selector** — three-mode chip group above the Detection Objects tree, **defaulting to `Mission branch`** (see [decisions/why-branch-scoped-default.md](../decisions/why-branch-scoped-default.md)):
   - **Mission branch** — single-select dropdown of top-level ontology branches; auto-defaults to the first branch when the tree loads. Sends `ontology_branch` and a deduplicated `text_prompts` list derived client-side from the branch (and its descendants) via [`promptsForBranch`](../../frontend/src/utils/promptsForBranch.ts). The Detection Objects tree below stays visible, filtered to the chosen branch, so the operator can further narrow the slice.
   - **Cherry-pick objects** — legacy hand-pick UX; the operator's tree selection becomes `text_prompts` verbatim.
   - **All branches** — explicit opt-out; flattens every branch into one prompt list (~131 prompts). Yellow warning banner reminds the operator this carries a higher false-positive rate.
   A status chip in the Models row shows the active mode and prompt count (e.g. `[Branch: Air] 18 prompts`, `[All branches] 131 prompts ⚠`).
3. **FMV upload** — multi-file MP4 + optional `.srt` sidecar, with `(model, prompt_mode)` selectors. `model=yolo26` stays available here and routes to the YOLOE FMV tracker.
4. **URL ingest** — fetches from a remote URL on the backend side.
5. **Feeds** — connect/disconnect HTTP polling feeds; per-feed event log.
6. **Recent uploads** — live `GET /api/ingest/uploads` listing with per-row status and a link to the resulting satellite pass / FMV clip.

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
- [decisions/why-branch-scoped-default.md](../decisions/why-branch-scoped-default.md)
- [decisions/why-deconflicted-detection-prompts.md](../decisions/why-deconflicted-detection-prompts.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
