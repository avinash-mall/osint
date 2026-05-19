# `backend/video_metadata.py` — Telemetry Extraction

**Path:** [backend/video_metadata.py](../../backend/video_metadata.py)
**Lines:** ~464
**Depends on:** `klvdata` (MISB ST 0601), `mp4parser`-style atom reads for GPMD, SRT parser

## Purpose

Pull per-frame telemetry from an FMV clip. Tries in order: MISB ST 0601 KLV embedded in the MPEG-TS, MP4 GPMD atom, an SRT sidecar, then a synthetic demo fixture for offline testing. Persists rows to `fmv_frames`.

## Why this design

- **Cascading fallback.** Real UAS clips come from many sources; not all have KLV. The ladder gets the best telemetry available without the operator having to know which their clip uses.
- **Footprint WKT** is computed per frame from `(sensor_lat, sensor_lon, target_lat, target_lon, fov_horizontal, fov_vertical)` so the map can render the view footprint over time.
- **`TelemetryMissingError`** is **the** explicit-failure case. The ingest router catches it and falls back to the synthetic fixture path so a clip with no telemetry still produces a navigable timeline. `FMV_DEMO_MODE=1` forces the fixture.

## Key symbols

- [`_fmv_demo_mode_enabled`](../../backend/video_metadata.py#L33).
- [`TelemetryMissingError`](../../backend/video_metadata.py#L44).
- [`extract_telemetry`](../../backend/video_metadata.py#L75) — the public entry; takes `(video_path, srt_path?)`.
- [`_extract_klv`](../../backend/video_metadata.py#L128) — MISB 0601 path.
- [`_extract_gpmd`](../../backend/video_metadata.py#L232) — MP4 GPMD atom.
- [`_extract_srt`](../../backend/video_metadata.py#L334) — sidecar `.srt` parser.
- [`_samples_to_rows`](../../backend/video_metadata.py#L367) — converts samples to `fmv_frames` tuple shape.
- [`_footprint_wkt`](../../backend/video_metadata.py#L408).
- [`_fixture_rows`](../../backend/video_metadata.py#L441) — synthetic demo rows when no real telemetry available.

## Cross-references

- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [frontend/workspace-fmv-player.md](../frontend/workspace-fmv-player.md)
