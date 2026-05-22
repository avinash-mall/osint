# Data Flow — Imagery Ingest

**Entry:** `POST /api/ingest` ([backend/routers/ingest.py](../../backend/routers/ingest.py)) · `POST /api/ingest/upload` · `POST /api/ingest/url`
**Worker:** `process_satellite_imagery` in [backend/worker_legacy.py](../../backend/worker_legacy.py)
**Inference target:** [inference-sam3/main.py](../../inference-sam3/main.py) `/detect`

## Purpose

Raw raster (GeoTIFF, NITF, Sentinel L2A, HLS-6, S1 GRD) → displayable detections in PostGIS.

## Six-step pipeline

1. **COG translate** — `gdal_translate -of COG` rewrites input to Cloud-Optimised GeoTIFF on shared `/data/imagery/processed/`. `titiler` needs COG for windowed reads.
2. **Catalog** — pass footprint as `MULTIPOLYGON` in PostGIS (`satellite_passes`); mirrored as `SatellitePass` node in Neo4j.
3. **Chipping** — slice into overlapping `INFERENCE_CHIP_SIZE`×`INFERENCE_CHIP_SIZE` chips (default 1008×1008, 25% overlap). RGB chips PNG; multispectral/SAR stay GeoTIFF to preserve band radiometry. See `chip_to_uint8_rgb` in [backend/worker_legacy.py](../../backend/worker_legacy.py).
4. **Inference dispatch** — `INFERENCE_CHIP_CONCURRENCY` chips POSTed to `inference-sam3:8001/detect` in parallel via thread pool. Each request: `metadata.modality`, sensor-resolved `text_prompts` (from `/api/ontology/default-prompts`), `enabled_layers` (e.g. `sam3, dota_obb, dinov3_sat`).
5. **Georeference** — pixel-space bboxes/OBBs warped to WGS84 via source CRS from COG. Mask RLE kept pixel-space; OBB coords emitted as `yolo_obb_normalized_xyxyxyxy` (see schema).
6. **Evidence rank** — backend scores source agreement, optional RemoteCLIP verifier margin, physical sanity checks, SAR proxy status → `evidence_score` / `evidence_tier`.
7. **Persist** — detections → PostGIS `detections`: mask RLE, embedding, parent class, original (open-vocab) class, confidence, review status, evidence metadata, chip provenance (URL + index), model/taxonomy version, coverage polygon.

## Modality dispatch

| Sensor selection in UI | `metadata.modality` | Pipeline inside inference |
|---|---|---|
| Optical (RGB) | `rgb` | SAM3 text/box prompts → DOTA-OBB → optional GDINO → DINOv3-SAT embed |
| Multispectral / Hyperspectral | `multispectral` | Prithvi flood + burn → SAM3 on RGB preview → optional 3-timestep crop classifier |
| SAR | `sar` | CFAR primary; optional TerraMind S1→S2 → SAM3 synthetic preview, evidence-capped + review-only unless corroborated |
| FMV | n/a → [data-flow-fmv.md](data-flow-fmv.md) | — |

## Key env knobs

| Variable | Default | Effect |
|---|---|---|
| `INFERENCE_CHIP_SIZE` / `INFERENCE_CHIP_OVERLAP` | 1008 / 252 | SAM3 chip geometry |
| `MAX_INFERENCE_CHIPS` | 256 | Worker cap (0 = full coverage) |
| `INFERENCE_CHIP_CONCURRENCY` | 1 | Parallel POSTs per pass |
| `INFERENCE_MAX_PENDING_CHIPS` | 32 | Bounded encoded-chip queue |
| `INFERENCE_CHIP_SPOOL_MAX_BYTES` | 4 MiB | Spill encoded chip to temp file above this |
| `INFERENCE_CHIP_TIMEOUT_S` | 600 | Per-request timeout |

Full env reference: [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md).

## Failure modes

- **Missing prompts** — omitted `metadata.text_prompts` → bounded precision defaults. Explicit `metadata.text_prompts: []` → HTTP 400 unless box prompts supplied. Ontology backend fallback opt-in via `SAM3_DEFAULT_PROMPT_SOURCE=ontology`; backend unreachable in that mode → 503.
- **No CRS** — worker logs, skips georeferencing; coords stay pixel-space; detection persisted with `null` footprint.
- **Inference timeout** — chip marked failed; pass continues. Failed chips visible in `/api/inference/dashboard`.
- **OBB extraction** — degenerate mask → HBB fallback (`edge_truncated=true`).

## Cross-references

- [operations/imagery-ingest-pipeline.md](../operations/imagery-ingest-pipeline.md) — launch from UI vs API
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md) — task internals
- [backend/detection-evidence.md](../backend/detection-evidence.md) — evidence tiering before persistence
- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md) — what `/detect` does with the chip
- [decisions/why-sar-confidence-cap.md](../decisions/why-sar-confidence-cap.md)
