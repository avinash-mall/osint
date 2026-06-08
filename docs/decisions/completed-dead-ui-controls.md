# Completed: dead UI controls (FMV fullscreen/export, admin "More" buttons)

**Date:** 2026-06-08
**Status:** adopted

## Decision

A frontend interactivity audit found four controls that rendered as clickable
but had no `onClick` (no defined behaviour). Resolved each:

1. **FMV "Fullscreen" button** ([FmvPlayer.tsx](../../frontend/src/components/FmvPlayer.tsx))
   — **implemented.** Toggles the Fullscreen API on `wrapperRef` (the div that
   holds `<video>` + the overlay `<canvas>`), so detection overlays stay aligned;
   the existing canvas-sync `ResizeObserver` re-fits on the resize.

2. **FMV "Export clip" button** — **implemented.** Downloads the clip's original
   source file. Backend now returns a `source_url` on the clip GET responses
   (`GET /api/fmv/clips`, `/{id}`) = `fmv_public_url(None, file_path)`. This is a
   **separate field from `stream_url`**: `stream_url` is the HLS playlist when the
   clip is transcoded (a manifest, not a downloadable video), whereas `source_url`
   always points at the original uploaded file under `/fmv/<rel>`. The button
   anchors `source_url` at `API_URL` and downloads it.

3. **Admin "More" button on promoted models** ([ModelsView.tsx](../../frontend/src/components/admin/ModelsView.tsx))
   — **removed.** There is no model-demote (or any other promoted-model) endpoint,
   so the menu had no action to host. The action cell now renders a muted `—`.

4. **Admin "More actions" button on job cards** ([ProcessingView.tsx](../../frontend/src/components/admin/ProcessingView.tsx))
   — **removed.** Celery jobs expose no cancel/retry/delete endpoint, so there was
   no action to wire. Removed the button (and the now-unused `MoreHorizontal`
   import in both files).

## Why this design

- **Implement where a real action exists, remove where it doesn't.** Fullscreen
  and clip export are concrete, supportable features → implemented. The two "More"
  menus had no backing endpoint and no specified behaviour → removing them is the
  honest fix; a disabled/“coming soon” button would imply a roadmap that isn't
  there. If a model-demote or job-cancel endpoint is added later, re-introduce a
  real menu then.

## What this touched

- `frontend/src/components/FmvPlayer.tsx`: `toggleFullscreen` + `exportClip`
  callbacks; `source_url` on the `Clip` type; wired both buttons.
- `frontend/src/components/admin/ModelsView.tsx`,
  `frontend/src/components/admin/ProcessingView.tsx`: removed dead buttons +
  unused imports.
- `backend/main.py`: `source_url` on the two clip GET responses.

## Scope note

The audit also confirmed (and deliberately left unchanged): the analytics
`ANALYTICS_ALLOW_FIXTURES` demo paths and ingest `allow_synthetic_telemetry`
(opt-in, env-gated, labelled), and the `ChipPipelineGrid` progress visual (derives
its grid from real aggregate job counts; only per-chip *positions* are
approximate because the backend stores counts, not per-chip state). No dangling
frontend→backend API calls were found.

## Cross-references

- [frontend/workspace-fmv-player.md](../frontend/workspace-fmv-player.md)
- [frontend/admin-models-and-processing.md](../frontend/admin-models-and-processing.md)
- [backend/main-app-entrypoint.md](../backend/main-app-entrypoint.md)
