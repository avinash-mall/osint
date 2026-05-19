# `backend/fmv_helpers.py` — HLS Transcode + ffprobe

**Path:** [backend/fmv_helpers.py](../../backend/fmv_helpers.py)
**Lines:** ~88
**Depends on:** `ffmpeg`/`ffprobe` on `$PATH` (provided by the backend image)

## Purpose

Three small responsibilities: turn an uploaded clip into an HLS playlist nginx can serve, probe the clip for FPS/duration/dimensions, and generate the public URL the frontend uses.

## Why this design

- **`ffmpeg -c copy` first.** When the source is already H.264 + AAC (typical UAS feed), stream-copy is near-instant. Re-encoding is only attempted on failure.
- **Probe is shallow.** Only the few fields the UI/worker actually consume are read; we don't capture the entire stream graph because the rest is unused and large.
- **Public URL is configurable** via `FMV_PUBLIC_URL_BASE` so deployments behind reverse proxies with extra hops produce correct URLs.

## Key symbols

- [`fmv_public_url`](../../backend/fmv_helpers.py#L17).
- [`probe_video`](../../backend/fmv_helpers.py#L30) — `{duration, fps, width, height}`.
- [`transcode_hls`](../../backend/fmv_helpers.py#L64) — writes `playlist.m3u8` to `clip_dir`, returns its path or `None` on failure.

## Failure modes

- `ffmpeg` missing → caller falls back to raw `.mp4` link (clip viewer still works, scrubbing is slower).
- Unsupported codec → re-encode attempted; if that also fails, raw passthrough as above.

## Cross-references

- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
