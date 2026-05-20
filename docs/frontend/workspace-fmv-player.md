# FMV Workspace — `FmvPlayer.tsx`

**Path:** [frontend/src/components/FmvPlayer.tsx](../../frontend/src/components/FmvPlayer.tsx)
**Lines:** ~88893 characters (~2300 lines TSX, the largest single component)

## Purpose

HLS video player with KLV telemetry synced to a side-by-side map and per-frame detection overlays. Operators pick the prompt mode (`pcs` / `yoloe`), watch tracks form, and click into individual frame×track detections.

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
- `GET /api/fmv/clips` / `/{id}` — clip listing + metadata
- `GET /api/fmv/clips/{id}/klv` — telemetry rows (used for the timeline + footprint)
- `GET /api/fmv/clips/{id}/detections` — per-frame detections
- WebSocket: `fmv_detections_complete` topic triggers refetch
- HLS segments: `http://localhost:3000/fmv/<clip_id>/playlist.m3u8`

## Key behaviors

- **Time sync.** As the HLS video plays, the current frame index is computed from `currentTime * fps`. Telemetry and detection overlays for that frame are filtered and rendered.
- **Track formation.** Tracks are colored consistently across frames. Hovering a track shows its trajectory.
- **Prompt mode change** triggers a new ingest run when the user re-submits.

## Cross-references

- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [backend/video-metadata-klv.md](../backend/video-metadata-klv.md)
- [backend/fmv-track-consolidation.md](../backend/fmv-track-consolidation.md)
- [inference/sam3-pcs-multiplex-video.md](../inference/sam3-pcs-multiplex-video.md)
- [inference/yoloe-tracker.md](../inference/yoloe-tracker.md)
