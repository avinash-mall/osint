# Data Flow — FMV Ingest

**Entry:** `POST /api/fmv/clips` ([backend/routers/fmv.py](../../backend/routers/fmv.py))
**Worker:** `process_fmv` in [backend/worker_legacy.py](../../backend/worker_legacy.py)
**Inference target:** [inference-sam3/main.py](../../inference-sam3/main.py) `/detect_video`

## Purpose

Full-motion video clip (typically MISB 0601 H.264 from UAS feed) → telemetry extraction → per-frame tracking → near-real-time results to clients.

## Pipeline

1. **Upload** — multipart `POST /api/fmv/clips` with `.mp4` + optional `.srt` sidecar. Writes to `/data/fmv/incoming/`, queues `worker.process_fmv`.
2. **HLS transcode** — [backend/fmv_helpers.py](../../backend/fmv_helpers.py) runs `ffmpeg -c copy -f hls -hls_time 4` (stream-copy when codec already H.264). Segments served at `http://localhost:3000/fmv/<clip_id>/playlist.m3u8` via nginx.
3. **Telemetry extraction** — [backend/video_metadata.py](../../backend/video_metadata.py) tries in order: MISB ST 0601 KLV (klvdata) → MP4 GPMD atom → SRT sidecar → synthetic demo fixture. Rows → `fmv_frames` (clip_id, frame_index, timestamp, telemetry JSON, footprint WKT).
4. **Inference dispatch** — worker POSTs clip + metadata to `inference-sam3:8001/detect_video`, consumes the **NDJSON stream**, one record per frame×track.
   - `metadata.prompt_mode = "pcs"` (default): SAM 3.1 multiplex — one request per text prompt, streams merged. Omitted prompts → bounded precision defaults (`vehicle`, `person`, `building`).
   - `metadata.prompt_mode = "yoloe"`: standalone YOLOE tracker; empty `text_prompts` → `-pf` (prompt-free), else `-seg` (text-promptable).
5. **Persist** — each row → PostGIS `fmv_detections`: frame_index, track_id, bbox, mask RLE, detector `source_layer`, embedding (first frame of each track only), confidence. Rows raw — window-seam + cross-prompt duplicates included.
6. **Consolidate** — `process_fmv` dispatches `worker.consolidate_fmv` ([backend/fmv-track-consolidation.md](../backend/fmv-track-consolidation.md)): re-associates clip's `fmv_detections` into stable clip-global tracks, votes one canonical class per track, soft-deletes cross-prompt per-frame duplicates. Without it FmvPlayer side panel grows one row per frame×window×prompt.
7. **Notify** — `process_fmv` + consolidation task each publish `fmv_detections_complete` on Redis pubsub; WebSocket router forwards to subscribed clients → refetch.

## Sequence (timeline)

```
client    backend            worker             inference-sam3       nginx (HLS)
  │  POST /api/fmv/clips      │                       │                  │
  │ ─────────────────────────►│                       │                  │
  │                           │ queue process_fmv     │                  │
  │ ◄─ 202 task_id ───────────│                       │                  │
  │                           │ ffmpeg HLS ─────────►│ (segments out)   │
  │                           │ klv extract           │                  │
  │                           │ POST /detect_video ──►│                  │
  │ <── GET playlist.m3u8 ────┼───────────────────────┼─────────────────►│
  │                           │ ◄── NDJSON stream     │                  │
  │                           │ insert fmv_detections │                  │
  │                           │ publish "complete"    │                  │
  │ <── WS fmv_detections_complete ─                  │                  │
```

## Per-frame record shape

Same per-detection schema as `/detect`, plus `frame_index` and `track_id`:

```json
{
  "frame_index": 47,
  "track_id": 3,
  "class": "person",
  "source_layer": "sam3",
  "bbox": [0.51, 0.62, 0.07, 0.13],
  "confidence": 0.84,
  "mask_rle": {"size":[H,W],"counts":"..."},
  "embedding": { "model": "facebook/dinov3-vitl16-pretrain-sat493m", "dim": 1024, "fp16_b64": "..." }
}
```

`embedding` populated only on first frame of each track.

## Cross-references

- [operations/fmv-ingest-pipeline.md](../operations/fmv-ingest-pipeline.md)
- [backend/fmv-track-consolidation.md](../backend/fmv-track-consolidation.md) — Hungarian assignment downstream of NDJSON stream
- [inference/sam3-pcs-multiplex-video.md](../inference/sam3-pcs-multiplex-video.md)
- [inference/yoloe-tracker.md](../inference/yoloe-tracker.md)
- [decisions/why-yoloe-replaced-amg.md](../decisions/why-yoloe-replaced-amg.md)
- [backend/video-metadata-klv.md](../backend/video-metadata-klv.md)
