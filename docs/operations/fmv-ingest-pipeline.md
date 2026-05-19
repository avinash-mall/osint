# Operations — FMV Ingest

## TL;DR

Upload an FMV clip via the **Admin → Upload imagery** (FMV tab) or directly:

```bash
curl -F "file=@drone.mp4" \
     -F "srt_sidecar=@drone.srt" \
     -b "sentinel_session=$COOKIE" \
     http://localhost:3000/api/fmv/clips
```

## What happens

1. Clip lands at `/data/fmv/<clip_id>/source.mp4`.
2. `ffmpeg -c copy -f hls -hls_time 4` produces HLS segments — nginx serves them at `/fmv/<clip_id>/playlist.m3u8`.
3. Telemetry extracted (KLV → GPMD → SRT → fixture); rows persisted to `fmv_frames`.
4. Worker POSTs to `inference-sam3:/detect_video` with the operator's chosen prompt mode.
5. NDJSON stream consumed; rows persisted to `fmv_detections`.
6. WS `fmv_detections_complete` fires; FMV workspace refetches.

See [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md) for the sequence diagram.

When PCS mode has no operator-supplied prompts, the backend uses the bounded precision fallback `["vehicle", "person", "building"]` (or `FMV_DEFAULT_PROMPTS`) rather than expanding all ontology prompts.

## Prompt-mode choice

| Mode | Engine | Use when |
|---|---|---|
| `pcs` *(default)* | SAM 3.1 multiplex | You have a small text-prompt set and want stable temporal masks |
| `yoloe` (empty prompts) | YOLOE-26x-seg-pf | "Find me anything that looks like a known object" |
| `yoloe` (text prompts) | YOLOE-26x-seg | Open text + faster than PCS |

See [decisions/why-yoloe-replaced-amg.md](../decisions/why-yoloe-replaced-amg.md).

## Cross-references

- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [backend/video-metadata-klv.md](../backend/video-metadata-klv.md)
- [backend/fmv-helpers-hls.md](../backend/fmv-helpers-hls.md)
- [backend/tracker-fmv.md](../backend/tracker-fmv.md)
- [frontend/workspace-fmv-player.md](../frontend/workspace-fmv-player.md)
