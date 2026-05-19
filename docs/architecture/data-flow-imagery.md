# Data Flow — Imagery Ingest

**Entry:** `POST /api/ingest` ([backend/routers/ingest.py](../../backend/routers/ingest.py)) · `POST /api/ingest/upload` · `POST /api/ingest/url`
**Worker:** `process_satellite_imagery` in [backend/worker_legacy.py](../../backend/worker_legacy.py)
**Inference target:** [inference-sam3/main.py](../../inference-sam3/main.py) `/detect`

## Purpose

Get a raw raster (GeoTIFF, NITF, Sentinel L2A, HLS-6, S1 GRD) from upload to displayable detections in PostGIS.

## Six-step pipeline

1. **COG translate** — `gdal_translate -of COG` rewrites the input to Cloud-Optimised GeoTIFF on the shared `/data/imagery/processed/` volume. Tile server (`titiler`) needs the COG format for on-the-fly windowed reads.
2. **Catalog** — pass footprint stored as `MULTIPOLYGON` in PostGIS (`satellite_passes` table); mirrored as a `SatellitePass` node in Neo4j for graph queries.
3. **Chipping** — slice into overlapping `INFERENCE_CHIP_SIZE`×`INFERENCE_CHIP_SIZE` chips (default 1008×1008, 25% overlap). RGB chips are PNG; multispectral and SAR stay GeoTIFF to preserve band radiometry. See `chip_to_uint8_rgb` in [backend/worker_legacy.py](../../backend/worker_legacy.py).
4. **Inference dispatch** — `INFERENCE_CHIP_CONCURRENCY` chips POSTed to `inference-sam3:8001/detect` in parallel via a thread pool. Each request includes the `metadata.modality`, sensor-resolved `text_prompts` (from `/api/ontology/default-prompts`), and `enabled_layers` (e.g. `sam3, dota_obb, dinov3_sat`).
5. **Georeference** — pixel-space bboxes and OBBs warped back to WGS84 lat/lon using the source CRS read from the COG. Mask RLE is preserved in pixel space; OBB coordinates are emitted in `yolo_obb_normalized_xyxyxyxy` (see schema).
6. **Persist** — detections written to PostGIS `detections` with mask RLE, embedding, parent class, original (open-vocab) class, confidence, review status, chip provenance (chip URL + index), model/taxonomy version, and coverage polygon.

## Modality dispatch

| Sensor selection in UI | `metadata.modality` | Pipeline inside inference |
|---|---|---|
| Optical (RGB) | `rgb` | SAM3 text/box prompts → DOTA-OBB → optional GDINO → DINOv3-SAT embed |
| Multispectral / Hyperspectral | `multispectral` | Prithvi flood + burn → SAM3 on RGB preview → optional 3-timestep crop classifier |
| SAR | `sar` | TerraMind S1→S2 → SAM3 on synthetic preview → confidence cap 0.85, `sar_proxy=true`, `review_status=review_candidate` |
| FMV | n/a (routes to [data-flow-fmv.md](data-flow-fmv.md)) | — |

## Key env knobs

| Variable | Default | Effect |
|---|---|---|
| `INFERENCE_CHIP_SIZE` / `INFERENCE_CHIP_OVERLAP` | 1008 / 252 | Match SAM3's intended chip geometry |
| `MAX_INFERENCE_CHIPS` | 256 | Worker-side cap (0 = full coverage) |
| `INFERENCE_CHIP_CONCURRENCY` | 1 | Parallel POSTs per pass |
| `INFERENCE_MAX_PENDING_CHIPS` | 32 | Bounded encoded-chip queue |
| `INFERENCE_CHIP_SPOOL_MAX_BYTES` | 4 MiB | Spill encoded chip to temp file above this size |
| `INFERENCE_CHIP_TIMEOUT_S` | 600 | Per-request timeout |

Full env reference: [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md).

## Failure modes

- **Missing prompts.** Omitted `metadata.text_prompts` use bounded precision defaults by default. Explicit empty `metadata.text_prompts: []` returns HTTP 400 unless box prompts are supplied. Ontology backend fallback is opt-in with `SAM3_DEFAULT_PROMPT_SOURCE=ontology`; backend unreachable in that mode returns 503.
- **No CRS.** Worker logs and skips georeferencing; detection coordinates remain pixel-space. The detection is still persisted with a `null` footprint.
- **Inference timeout.** Chip is marked failed; pass continues. Failed chips are visible in `/api/inference/dashboard`.
- **OBB extraction.** When the mask is degenerate, the worker falls back to HBB (`edge_truncated=true`).

## Cross-references

- [operations/imagery-ingest-pipeline.md](../operations/imagery-ingest-pipeline.md) — how to launch from UI vs API
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md) — task internals
- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md) — what `/detect` does once it has the chip
- [decisions/why-sar-confidence-cap.md](../decisions/why-sar-confidence-cap.md)
