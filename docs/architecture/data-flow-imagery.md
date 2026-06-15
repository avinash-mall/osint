# Data Flow — Imagery Ingest

**Entry:** `POST /api/ingest` ([backend/routers/ingest.py](../../backend/routers/ingest.py)) · `POST /api/ingest/upload` · `POST /api/ingest/url`
**Worker:** `process_satellite_imagery` in [backend/worker_legacy.py](../../backend/worker_legacy.py)
**Inference target:** [inference-sam3/main.py](../../inference-sam3/main.py) `/detect`

## Purpose

Raw raster (GeoTIFF, NITF, Sentinel L2A, HLS-6, S1 GRD) → displayable detections in PostGIS.

## Six-step pipeline

1. **COG translate** — `gdal_translate -of COG` rewrites input to Cloud-Optimised GeoTIFF on shared `/data/imagery/processed/`. `titiler` needs COG for windowed reads.
2. **Catalog** — pass footprint as `MULTIPOLYGON` in PostGIS (`satellite_passes`); mirrored as `SatellitePass` node in Neo4j.
3. **Chipping** — slice into overlapping `INFERENCE_CHIP_SIZE`×`INFERENCE_CHIP_SIZE` chips (default 1008×1008, 25% overlap). RGB chips PNG; multispectral/SAR stay GeoTIFF to preserve band radiometry. See `chip_to_uint8_rgb` in [backend/worker_legacy.py](../../backend/worker_legacy.py). Two optional extra passes share the **same dedupe index** as the main grid (NMS/WBF suppresses cross-scale duplicates): a **small-object pass** at a finer `INFERENCE_SMALL_OBJECT_CHIP_SIZE` (more pixels-per-object on small targets) and a single opt-in **full-scene pass** (`INFERENCE_FULL_SCENE_PASS=1`) over the whole image read decimated from COG overviews (catches objects larger than one chip — runways, piers). See [decisions/multi-scale-and-full-scene-chip-passes.md](../decisions/multi-scale-and-full-scene-chip-passes.md). With `INFERENCE_PAD_CHIPS_TO_SIZE=1`, variable-size edge chips are padded up to `chip_size²` so SAM3's `torch.compile` graph applies to every chip (see [decisions/sam3-compile-and-chip-padding-2026-06-14.md](../decisions/sam3-compile-and-chip-padding-2026-06-14.md)).
4. **Inference dispatch** — `INFERENCE_CHIP_CONCURRENCY` chips POSTed to `inference-sam3:8001/detect` in parallel via thread pool. Each request: `metadata.modality`, sensor-resolved `text_prompts` (from `/api/ontology/default-prompts`), `enabled_layers` (e.g. `sam3, dota_obb, dinov3_sat`).
5. **Georeference** — pixel-space bboxes/OBBs warped to WGS84 via source CRS from COG. Mask RLE kept pixel-space; OBB coords emitted as `yolo_obb_normalized_xyxyxyxy` (see schema).
6. **Evidence rank** — backend scores source agreement, optional semantic-verifier margin (generic plumbing; no active RemoteCLIP producer), physical sanity checks, SAR proxy status → `evidence_score` / `evidence_tier`.
7. **Persist** — detections → PostGIS `detections`: mask RLE, embedding, parent class, original (open-vocab) class, confidence, review status, evidence metadata, chip provenance (URL + index), model/taxonomy version, coverage polygon.

## Modality dispatch

| Sensor selection in UI | `metadata.modality` | Pipeline inside inference |
|---|---|---|
| Optical (RGB) | `rgb` | SAM3 text/box prompts → DOTA-OBB → MVRSD → DINOv3-SAT embed |
| Multispectral / Hyperspectral | `multispectral` | SAM3 on RGB preview (HLS-6 → 3/2/1 stretch) → DINOv3-SAT embed |
| SAR | `sar` | CFAR primary; optional TerraMind S1→S2 → SAM3 synthetic preview, evidence-capped + review-only unless corroborated |
| FMV | n/a → [data-flow-fmv.md](data-flow-fmv.md) | — |

## Key env knobs

| Variable | Default | Effect |
|---|---|---|
| `INFERENCE_CHIP_SIZE` / `INFERENCE_CHIP_OVERLAP` | 1008 / 252 | SAM3 chip geometry |
| `INFERENCE_SMALL_OBJECT_CHIP_SIZE` | 0 (off) | Finer second-pass chip size for small targets |
| `INFERENCE_FULL_SCENE_PASS` | 0 (off) | One extra whole-image decimated pass for large objects |
| `MAX_INFERENCE_CHIPS` | 256 | Worker cap (0 = full coverage) |
| `INFERENCE_CHIP_CONCURRENCY` | 1 | Parallel POSTs per pass |
| `INFERENCE_MAX_PENDING_CHIPS` | 32 | Bounded encoded-chip queue (in-flight ceiling) |
| `INFERENCE_MIN_PENDING_CHIPS` | 4 | Adaptive back-off floor — keeps the GPU-replica pool fed; set to inference GPU count |
| `INFERENCE_CHIP_SPOOL_MAX_BYTES` | 4 MiB | Spill encoded chip to temp file above this |
| `INFERENCE_CHIP_TIMEOUT_S` | 600 | Per-request timeout |

Full env reference: [deployment/environment-variables-reference.md](../deployment/environment-variables-reference.md).

## Failure modes

- **Missing prompts** — omitted `metadata.text_prompts` → bounded precision defaults. Explicit `metadata.text_prompts: []` → HTTP 400 unless box prompts supplied. Ontology backend fallback opt-in via `SAM3_DEFAULT_PROMPT_SOURCE=ontology`; backend unreachable in that mode → 503.
- **No CRS** — worker logs, skips georeferencing; coords stay pixel-space; detection persisted with `null` footprint.
- **Inference timeout** — chip marked failed; pass continues. Failed chips visible in `/api/inference/dashboard`.
- **OBB extraction** — degenerate mask → HBB fallback (`edge_truncated=true`).

## Wall-time note

Total ingest wall-time is dominated by the post-inference **detection-tracker**
stage (`update_tracks_for_pass` in [worker_legacy.py](../../backend/worker_legacy.py)),
not GPU inference. The tracker associates the new pass's detections against the
existing track/detection store, so its cost scales with *resident* detection count
— measured ~4 s on an empty store vs ~10 min with ~26k resident on the al_udeid
scene (full coverage). Inference-side optimizations (compile, deferred embedding)
land in the chip POST→dedupe phase; to see them in total wall-time, measure on a
small/clean store. See [decisions/defer-embedding-to-post-fusion-2026-06-15.md](../decisions/defer-embedding-to-post-fusion-2026-06-15.md).

## Cross-references

- [inference/sahi-equivalence.md](../inference/sahi-equivalence.md) — chipping IS our SAHI; SAHI↔env mapping + small-object boost
- [operations/imagery-ingest-pipeline.md](../operations/imagery-ingest-pipeline.md) — launch from UI vs API
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md) — task internals
- [backend/detection-evidence.md](../backend/detection-evidence.md) — evidence tiering before persistence
- [inference/sam3-runner-internals.md](../inference/sam3-runner-internals.md) — what `/detect` does with the chip
- [decisions/why-sar-confidence-cap.md](../decisions/why-sar-confidence-cap.md)
