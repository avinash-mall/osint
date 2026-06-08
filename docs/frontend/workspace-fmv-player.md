# FMV Workspace — `FmvPlayer.tsx`

**Path:** [frontend/src/components/FmvPlayer.tsx](../../frontend/src/components/FmvPlayer.tsx)
**Lines:** ~88893 characters (~2300 lines TSX, the largest single component)

## Purpose

HLS video player with KLV telemetry synced to a side-by-side map + per-frame detection overlays. Operators pick the prompt mode (`pcs` / `yoloe`), watch tracks form, click into individual frame×track detections.

## Layout

```
┌────────────────────────────────────┬───────────────────────────────┐
│  HLS video element                 │  Mini-map (Leaflet)           │
│  + detection bbox overlays         │  + sensor footprint polygon   │
│  + KLV-derived target reticle      │  + selected detection marker  │
├────────────────────────────────────┴───────────────────────────────┤
│  Timeline / scrubber + telemetry rows                              │
│  Prompt-mode picker (PCS / YOLOE / prompt-free)                    │
└────────────────────────────────────────────────────────────────────┘
```

## Data sources

- `POST /api/fmv/clips` (uploads new clips; lives in [backend-routers/ingest-router.md](../backend-routers/ingest-router.md))
- `GET /api/fmv/clips` / `/{id}` — clip listing + metadata. Each clip carries `stream_url` (HLS playlist when transcoded, else the raw file) and `source_url` (always the original file's `/fmv/<rel>` URL, used by the **Export clip** button).
- `GET /api/fmv/clips/{id}/klv` — telemetry rows (timeline + footprint)
- `GET /api/fmv/clips/{id}/detections` — per-frame detections
- WebSocket: `fmv_detections_complete` triggers refetch
- HLS segments: `http://localhost:3000/fmv/<clip_id>/playlist.m3u8`

## Key behaviors

- **Side-panel default tab** — on a fresh visit (no `crossNav.fmvClipId`) the right panel opens on the **Clips** library so an analyst who just uploaded a clip sees it without hunting through sub-tabs. Cross-nav from the map (which carries a clip id) defaults to **Tracks** instead, matching the analysis intent. See [decisions/fmv-default-sidetab-clips.md](../decisions/fmv-default-sidetab-clips.md).
- **Time sync** — as the HLS video plays, current frame index = `currentTime * fps`. Telemetry + detection overlays for that frame filtered and rendered.
- **Track formation** — tracks colored consistently across frames. Hovering a track shows its trajectory.
- **Prompt mode change** triggers a new ingest run when the user re-submits.
- **HUD readouts** sit on a translucent backplate for WCAG-AA contrast over bright video (UX-AUDIT F19).
- **PiP map** expands to split view on double-click of its header bar, in addition to the maximise button (F20).
- **Fullscreen** — the video-pane overlay Maximize button calls the Fullscreen API on `wrapperRef` (the div holding `<video>` + the overlay canvas), so detection overlays stay aligned in fullscreen via the existing canvas-sync `ResizeObserver`.
- **Export clip** — the transport-bar "Clip" button downloads the clip's original source file via `source_url` (anchored at `API_URL`), filename derived from `file_path`; disabled when no clip is selected.
- **Keyboard shortcuts** — `Space`/`K` play-pause, `←`/`→` step frame, `J`/`L` fast scrub, `?` opens a `KeyboardShortcutSheet` overlay listing them (F21).
- **Clip-load error surface** — `fetchClips` failures (network, 5xx, parse) set a `clipsError` state that the Clips tab renders as *"Failed to load clips: …"* in `var(--crit)` with a Retry button, instead of falling back to the misleading "No clips yet" empty state.

## Cross-references

- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [decisions/ux-audit-001.md](../decisions/ux-audit-001.md)
- [backend/video-metadata-klv.md](../backend/video-metadata-klv.md)
- [backend/fmv-track-consolidation.md](../backend/fmv-track-consolidation.md)
- [inference/sam3-pcs-multiplex-video.md](../inference/sam3-pcs-multiplex-video.md)
- [inference/yoloe-tracker.md](../inference/yoloe-tracker.md)
