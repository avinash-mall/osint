# `backend/fmv_helpers.py` — HLS Transcode + ffprobe

**Path:** [backend/fmv_helpers.py](../../backend/fmv_helpers.py)
**Lines:** ~88
**Depends on:** `ffmpeg`/`ffprobe` on `$PATH` (provided by backend image)

## Purpose

Three responsibilities: uploaded clip → HLS playlist nginx can serve; probe clip for FPS/duration/dimensions; generate the public URL the frontend uses.

## Why this design

- **`ffmpeg -c copy` first** — source already H.264 + AAC (typical UAS feed) → stream-copy near-instant. Re-encode only on failure.
- **Shallow probe** — only the few fields UI/worker consume; full stream graph unused and large.
- **Public URL configurable** via `FMV_PUBLIC_URL_BASE` — correct URLs for deployments behind reverse proxies with extra hops.

## Key symbols

- [`fmv_public_url`](../../backend/fmv_helpers.py#L17).
- [`probe_video`](../../backend/fmv_helpers.py#L30) — `{duration, fps, width, height}`.
- [`transcode_hls`](../../backend/fmv_helpers.py#L64) — writes `playlist.m3u8` to `clip_dir`; returns path or `None` on failure.

## Failure modes

- `ffmpeg` missing → caller falls back to raw `.mp4` link (viewer works, scrubbing slower).
- Unsupported codec → re-encode attempted; if that fails too, raw passthrough.

## Cross-references

- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
