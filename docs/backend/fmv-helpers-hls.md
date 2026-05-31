# `backend/fmv_helpers.py` — HLS Transcode + ffprobe

**Path:** [backend/fmv_helpers.py](../../backend/fmv_helpers.py)
**Lines:** ~99
**Depends on:** `ffmpeg`/`ffprobe` on `$PATH` (provided by backend image), env `FMV_PROBE_TIMEOUT_S`, `FMV_TRANSCODE_TIMEOUT_S`

## Purpose

Three responsibilities: uploaded clip → HLS playlist nginx can serve; probe clip for FPS/duration/dimensions; generate the public URL the frontend uses.

## Why this design

- **`ffmpeg -c copy` first** — source already H.264 + AAC (typical UAS feed) → stream-copy near-instant. If ffmpeg fails or times out, keep the raw clip cataloged.
- **Shallow probe** — only the few fields UI/worker consume; full stream graph unused and large.
- **Public URL configurable** via `FMV_PUBLIC_URL_BASE` — correct URLs for deployments behind reverse proxies with extra hops.
- **Subprocess timeouts are env-driven** — malformed media should not pin an API worker indefinitely.

## Key symbols

- [`_env_int`](../../backend/fmv_helpers.py#L17-L21) — integer env reader for subprocess timeouts.
- [`fmv_public_url`](../../backend/fmv_helpers.py#L24-L34).
- [`probe_video`](../../backend/fmv_helpers.py#L37-L69) — `{duration, fps, width, height}` with `FMV_PROBE_TIMEOUT_S`.
- [`transcode_hls`](../../backend/fmv_helpers.py#L72-L99) — writes `index.m3u8` to `clip_dir`; returns path or `None` on failure/timeout.

## Inputs / Outputs

`probe_video(path)` accepts a local video `Path` and returns the metadata dict consumed by FMV catalog rows. `transcode_hls(input_path, clip_dir)` accepts a local clip and output directory, returning the playlist path when HLS creation succeeds or `None` when the raw clip should be used.

## Failure modes

- `ffprobe` missing, malformed, or timed out → zero/unknown metadata rather than request failure.
- `ffmpeg` missing, unsupported, or timed out → caller falls back to the raw clip link (viewer works, scrubbing slower).
- Raw fallback avoids copying the clip onto itself when the source already lives in `clip_dir`.

## Cross-references

- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
- [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md)
- [decisions/why-security-hardening-2026-05-31.md](../decisions/why-security-hardening-2026-05-31.md)
