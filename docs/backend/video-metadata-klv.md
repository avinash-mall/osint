# `backend/video_metadata.py` — Telemetry Extraction

**Path:** [backend/video_metadata.py](../../backend/video_metadata.py)
**Lines:** ~464
**Depends on:** `klvdata` (MISB ST 0601), `mp4parser`-style atom reads for GPMD, SRT parser

## Purpose

Pull per-frame telemetry from an FMV clip. Tries in order: MISB ST 0601 KLV in the MPEG-TS → MP4 GPMD atom → SRT sidecar → synthetic demo fixture (offline testing). Persists rows to `fmv_frames`.

## Why this design

- **Cascading fallback** — real UAS clips come from many sources; not all have KLV. The ladder gets the best telemetry available without the operator knowing which their clip uses.
- **Footprint WKT** computed per frame from `(sensor_lat, sensor_lon, target_lat, target_lon, fov_horizontal, fov_vertical)` → map renders the view footprint over time. The East-West half-span divides by metres-per-degree-lon (`111320·cos(lat)`) and the North-South half-span by `111320` (these divisors were previously swapped, distorting every footprint by a `cos(lat)` factor).
- **KLV without absolute timestamps** — when packets carry no PrecisionTimeStamp, samples are spread evenly across the real clip `duration_s` (not [0,1)s, which collapsed them into the first second of frames and let the per-frame dedup discard most).
- **`TelemetryMissingError` = the explicit-failure case** — ingest router catches it, falls back to the synthetic fixture path so a clip with no telemetry still produces a navigable timeline. `FMV_DEMO_MODE=1` forces the fixture.

## Key symbols

- [`_fmv_demo_mode_enabled`](../../backend/video_metadata.py#L33).
- [`TelemetryMissingError`](../../backend/video_metadata.py#L44).
- [`extract_telemetry`](../../backend/video_metadata.py#L75) — public entry; takes `(video_path, srt_path?)`.
- [`_extract_klv`](../../backend/video_metadata.py#L128) — MISB 0601 path.
- [`_extract_gpmd`](../../backend/video_metadata.py#L232) — MP4 GPMD atom.
- [`_extract_srt`](../../backend/video_metadata.py#L334) — sidecar `.srt` parser.
- [`_samples_to_rows`](../../backend/video_metadata.py#L367) — samples → `fmv_frames` tuple shape.
- [`_footprint_wkt`](../../backend/video_metadata.py#L408).
- [`_fixture_rows`](../../backend/video_metadata.py#L441) — synthetic demo rows when no real telemetry.

## Cross-references

- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [frontend/workspace-fmv-player.md](../frontend/workspace-fmv-player.md)
