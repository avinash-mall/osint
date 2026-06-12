# Operations — FMV Ingest

## TL;DR

Upload an FMV clip via **Admin → Upload imagery** (FMV tab) or directly:

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
5. NDJSON stream consumed; raw rows persisted to `fmv_detections`.
6. `worker.consolidate_fmv` runs (`default` queue) — re-associates the clip's detections into stable clip-global tracks, votes one class per track, soft-deletes cross-prompt per-frame duplicates. See [backend/fmv-track-consolidation.md](../backend/fmv-track-consolidation.md).
7. WS `fmv_detections_complete` fires (from `process_fmv` and again after consolidation); FMV workspace refetches.

See [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md) for the sequence diagram.

PCS mode with no operator-supplied prompts → backend uses the bounded precision fallback `["vehicle", "person", "building"]` (or `FMV_DEFAULT_PROMPTS`), not all ontology prompts.

**Failure semantics:** each (window, prompt) inference task retries once across an inference self-heal restart; the clip is marked `failed` only when every window fails extraction (corrupt video) or more than `FMV_MAX_FAILED_TASK_FRACTION` (default 5%) of tasks fail. Partial failures are recorded as `tracking_windows_failed` in the clip metadata and consolidation still runs on what landed. See [decisions/audit-fixes-worker-2026-06-11.md](../decisions/audit-fixes-worker-2026-06-11.md).

## Prompt-mode choice

| Mode | Engine | Use when |
|---|---|---|
| `pcs` *(default)* | SAM 3.1 multiplex | Small text-prompt set, want stable temporal masks |
| `yoloe` (empty prompts) | YOLOE-26x-seg-pf | "Find me anything that looks like a known object" |
| `yoloe` (text prompts) | YOLOE-26x-seg | Open text + faster than PCS |

See [decisions/why-yoloe-replaced-amg.md](../decisions/why-yoloe-replaced-amg.md).

## Cross-references

- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [backend/video-metadata-klv.md](../backend/video-metadata-klv.md)
- [backend/fmv-helpers-hls.md](../backend/fmv-helpers-hls.md)
- [backend/fmv-track-consolidation.md](../backend/fmv-track-consolidation.md)
- [frontend/workspace-fmv-player.md](../frontend/workspace-fmv-player.md)
