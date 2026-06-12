# `backend/video_metadata.py` — Telemetry Extraction

**Path:** [backend/video_metadata.py](../../backend/video_metadata.py)
**Lines:** ~478
**Depends on:** `klvdata` (MISB ST 0601), `mp4parser`-style atom reads for GPMD, SRT parser

## Purpose

Pull per-frame telemetry from an FMV clip. Tries in order: MISB ST 0601 KLV in the MPEG-TS → MP4 GPMD atom → SRT sidecar → synthetic demo fixture (offline testing). Persists rows to `fmv_frames`.

## Why this design

- **Cascading fallback** — real UAS clips come from many sources; not all have KLV. The ladder gets the best telemetry available without the operator knowing which their clip uses.
- **Footprint WKT** computed per frame from `(sensor_lat, sensor_lon, target_lat, target_lon, fov_horizontal, fov_vertical)` → map renders the view footprint over time. The corner rectangle is rotated in **metres first** with the clockwise-from-north azimuth convention, then converted to degrees (East divides by `111320·cos(lat)`, North by `111320`) — rotating in degree space distorted the box anisotropically away from the equator, and the previous rotation was CCW.
- **KLV/GPMD without absolute timestamps** — when packets carry no PrecisionTimeStamp (KLV) or per-sample time (GPMD GPS5), samples are spread evenly across the real clip `duration_s` (not [0,1)s, which collapsed them into the first second of frames and let the per-frame dedup discard most). Both extractors take `duration_s`.
- **`TelemetryMissingError` = the explicit-failure case** — ingest router catches it, falls back to the synthetic fixture path so a clip with no telemetry still produces a navigable timeline. `FMV_DEMO_MODE=1` forces the fixture.

## Key symbols

- [`_fmv_demo_mode_enabled`](../../backend/video_metadata.py#L33).
- [`TelemetryMissingError`](../../backend/video_metadata.py#L44).
- [`extract_telemetry`](../../backend/video_metadata.py#L75) — public entry; takes `(video_path, srt_path?)`.
- [`_extract_klv`](../../backend/video_metadata.py#L128) — MISB 0601 path.
- [`_extract_gpmd`](../../backend/video_metadata.py#L236) — MP4 GPMD atom; takes `duration_s` like `_extract_klv`.
- [`_extract_srt`](../../backend/video_metadata.py#L342) — sidecar `.srt` parser.
- [`_samples_to_rows`](../../backend/video_metadata.py#L375) — samples → `fmv_frames` tuple shape.
- [`_footprint_wkt`](../../backend/video_metadata.py#L416).
- [`_fixture_rows`](../../backend/video_metadata.py#L455) — synthetic demo rows when no real telemetry.

## Cross-references

- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [frontend/workspace-fmv-player.md](../frontend/workspace-fmv-player.md)
- [decisions/audit-fixes-backend-core-2026-06-11.md](../decisions/audit-fixes-backend-core-2026-06-11.md) — GPMD duration spread, footprint rotation
- Tests: [backend/tests/test_video_metadata_gpmd.py](../../backend/tests/test_video_metadata_gpmd.py)
