# `scripts/build_triage_set.py` — Production-Image Triage Benchmark Builder

**Path:** [scripts/build_triage_set.py](../../scripts/build_triage_set.py)
**Lines:** ~508
**Depends on:** `rasterio`, `Pillow`, `pyyaml`, `requests`; reads
`/data/imagery/processed/*_cog.tif` (mounted via the `imagery_data` docker
volume) or the backend's `GET /api/ingest/uploads` endpoint.
Also depends on [scripts/eval_datasets/triage.py](../../scripts/eval_datasets/triage.py)
for the consumer side.

## Purpose

Tier 0 of the detection-quality plan: build a small (~10–50 chip) benchmark
from the operator's own recent ingested imagery, paired with an
analyst-edited `annotations.yaml` template. Every subsequent change to the
detection stack is measured by re-running
`scripts/compare_inference_layers.py --triage-set <dir>` and comparing
per-class precision / recall against the previous run.

## Why this design

The existing benchmark
([docs/benchmarks/detection-quality-ontology-mode-2026-05-22.md](../benchmarks/detection-quality-ontology-mode-2026-05-22.md))
measures DOTA-v1.0 val — useful for academic comparison but blind to the
operator's actual sensor mix, target classes, and chip statistics. Without a
benchmark on the operator's own imagery, every claimed improvement to the
stack is unverified for the real workload.

The script reads directly from the mounted `/data/imagery/processed`
directory by default to sidestep the session-cookie / CSRF dance the
`/api/ingest/uploads` endpoint requires. API mode is kept as a fallback for
hosts where the volume is not mounted.

Chips are sized 1008 px to match the production
`INFERENCE_CHIP_SIZE` ([backend/worker_legacy.py#L136](../../backend/worker_legacy.py#L136))
and re-stretched with the same 2/98-percentile contrast as
`chip_to_uint8_rgb` so the triage chips look like what the detector actually
sees, not the raw COG.

## Key symbols

- [`_pick_recent_uploads(data_dir, max_uploads)`](../../scripts/build_triage_set.py#L73-L84) — newest-mtime `*_cog.tif` sort.
- [`_upload_id_from_cog(cog_path)`](../../scripts/build_triage_set.py#L87-L97) — extracts the `<upload-id>` prefix.
- [`_extract_chips_from_cog(cog_path, chips_per_upload)`](../../scripts/build_triage_set.py#L100-L156) — windowed read + percentile stretch; caps at 1 chip when the raster is smaller than the chip size (logged).
- [`_pick_recent_uploads_via_api(api_url, session_cookie, max_uploads)`](../../scripts/build_triage_set.py#L183-L203) — API-mode fallback.
- [`_normalise_sources(sources)`](../../scripts/build_triage_set.py#L241-L293) — collapses data-dir / api inputs to uniform `(cog_path, upload_id, extra_meta)` triples; detects upload_id collisions and skips later colliders with a warning.
- [`_write_triage_set(out_dir, sources, chips_per_upload, dry_run)`](../../scripts/build_triage_set.py#L296-L351) — single shared write loop over normalised sources; emits chips + sidecars + `annotations.yaml` + `README.md`.
- [`_write_png(path, rgb_array)`](../../scripts/build_triage_set.py#L354-L357) — Pillow PNG writer (required dependency, no stdlib fallback).
- [`main(argv)`](../../scripts/build_triage_set.py#L449-L503) — CLI entry point; `--triage-set` flag on
  the comparison driver consumes the output.

## Inputs / Outputs

**Inputs:**
- `--source data-dir` (default): scans `--data-dir` (default
  `/data/imagery/processed`) for `*_cog.tif` files, mtime-sorted.
- `--source api`: hits `GET /api/ingest/uploads` with `--session-cookie` or
  `$SENTINEL_SESSION_COOKIE`. Uses the row's `file_path` to find the COG.
- `--max-uploads N` (default 50), `--chips-per-upload N` (default 2),
  `--rgb-only` / `--include-non-rgb`, `--dry-run`.

**Outputs:** under `--out` (default `bench/triage/<YYYY-MM-DD>`):
- `chips/<upload-id>_<idx>.png` — RGB chip.
- `chips/<upload-id>_<idx>.json` — per-chip metadata (modality, sensor,
  branch, source COG, window).
- `annotations.yaml` — analyst-fillable template with one row per chip.
- `README.md` — annotation + scoring instructions.

## Failure modes

- Empty data-dir → warning, exit 0 (no crash).
- API mode without a cookie → `RuntimeError` from
  `_pick_recent_uploads_via_api`.
- Two COGs whose pre-underscore prefix matches → warning, second one skipped
  (chip filenames would otherwise overwrite silently).
- Raster narrower than the 1008 px chip size → emits a single chip with an
  info log; the request for N chips is honoured down to 1.
- Pillow missing → import-time `ImportError`. Pillow is a required dep.
- Backend not reachable in API mode → `requests` exception propagates.

## Cross-references

- Consumer: [scripts/eval_datasets/triage.py](../../scripts/eval_datasets/triage.py)
- Driver wiring: [scripts/compare-inference-layers.md](compare-inference-layers.md)
- Related benchmark: [benchmarks/detection-quality-ontology-mode-2026-05-22.md](../benchmarks/detection-quality-ontology-mode-2026-05-22.md)
- Upload endpoint: [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- Chip extraction reference: [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
