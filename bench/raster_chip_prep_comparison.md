# Raster Chip-Prep Optimization — Phase Comparison

Companion to `bench/raster_chip_prep_*.json`. Each phase here mirrors the plan
at `.claude/plans/the-prepared-raster-chips-effervescent-orbit.md`.

**Scope:** every stage between raster-on-disk and the model forward pass.
Forward pass itself is tuned by the parallel `bench/sam3_*.json` track and is
out of scope here.

## How to capture a new snapshot

The benchmark drives `worker.slice_and_infer` against a real Cloud-Optimized
GeoTIFF and talks to a running inference-sam3 instance. It must run on a host
where both are reachable.

```bash
# 1. Inference service reachable. From the host network:
#    docker ps  # confirm osint-inference-sam3-1 is "Up" and "healthy"
#    The container exposes 8001 inside the docker network only; resolve its
#    bridge IP with:  docker inspect osint-inference-sam3-1 \
#       --format '{{(index .NetworkSettings.Networks "osint_default").IPAddress}}'

# 2. Pick a representative production COG and export its path:
export CHIP_PREP_BENCH_FIXTURE=/data/imagery/processed/<pick-one>.tif
export INFERENCE_SAM3_URL=http://<container-ip>:8001

# 3. Run the benchmark (writes bench/raster_chip_prep_<label>.json):
python scripts/benchmark_chip_prep.py --label baseline

# 4. For per-chip CSVs (offline analysis), use the profile script:
python scripts/profile_chip_prep.py --csv /tmp/chip_prep_events.csv
```

The benchmark sets `CHIP_PREP_PROFILE=1` for its child process; the
`chip_prep_profiler` module's per-stage histograms feed the JSON output.

## Fixture choice

`CHIP_PREP_BENCH_FIXTURE` must be a real COG on the host that the worker
container would also see. Three input shapes are worth re-running:

| Class            | Why it matters                                                  |
|------------------|------------------------------------------------------------------|
| Large RGB aerial | dominates the PNG-encode and post-roundtrip stages              |
| Multispectral    | exercises the GeoTIFF MemoryFile path in `_emit_chip_payload`   |
| Sentinel-1 SAR   | exercises the 2-band VV/VH GeoTIFF path + TerraMind on the server |

## Per-stage histogram keys

`chip_prep_profiler` records into these buckets (sample counts equal the number
of chips that reached that stage):

| Stage                  | Wraps                                                |
|------------------------|------------------------------------------------------|
| `valid_mask`           | `valid_data_mask(src, window)`                       |
| `read_probe`           | `src.read(window=...)` (nodata / all-zero probe)     |
| `encode`               | `_emit_chip_payload(...)` (PNG or GeoTIFF branch)    |
| `encode_png`           | PIL `Image.save(format="PNG")` (RGB branch only)     |
| `encode_geotiff_read`  | `src.read(indexes=...)` inside `_geotiff_window_file`|
| `encode_geotiff_write` | `MemoryFile` write + spool (MSI/SAR branches)        |
| `submit`               | `executor.submit(_post_chip_to_sam3, ...)`           |
| `post_roundtrip`       | wall-time from submit to consumed `fut.result()`     |
| `apply_response`       | `_apply_chip_response` per-chip projection           |
| `dedupe`               | `dedupe_idx.add(chip_dets)` (NMS or WBF)             |

## Phase ladder (filled in as each phase lands)

| Phase                       | JSON                                      | chips/sec | p50 post_roundtrip | p50 encode | notes |
|-----------------------------|-------------------------------------------|----------:|-------------------:|-----------:|-------|
| 0 baseline                  | `raster_chip_prep_baseline.json`          | —         | —                  | —          | infra only; capture on host with imagery |
| 1 env + ensure_cog          | `raster_chip_prep_phase1.json`            | —         | —                  | —          | GDAL_CACHEMAX, ZSTD, concurrency↑ |
| 2 block-aligned + mask fuse | `raster_chip_prep_phase2.json`            | —         | —                  | —          | tile-aligned sampler, single mask read |
| 3 parallel readers          | `raster_chip_prep_phase3.json`            | —         | —                  | —          | reader pool decoupled from poster pool |
| 4 `/detect_raw` transport   | `raster_chip_prep_phase4.json`            | —         | —                  | —          | skip PNG + MemoryFile round-trip |
| 5 parallel download         | `raster_chip_prep_phase5.json`            | —         | —                  | —          | range-GET fan-out |
| 6 GPU decode (gated)        | `raster_chip_prep_phase6.json`            | —         | —                  | —          | nvImageCodec/nvJPEG2000/nvTIFF |
| 7 vectorized dedupe         | `raster_chip_prep_phase7.json`            | —         | —                  | —          | KDTree-bucketed NMS |

## Verification protocol

For every phase change:

1. Run the same fixture against the same inference container (same model
   manifest, same speed profile).
2. Record `bench/raster_chip_prep_<label>.json`.
3. Update this table.
4. Hash the bytes the worker would have POSTed for five random chip indices
   (same indices each phase) and confirm they match the baseline. This is the
   pixel-identity gate — encoded representation can change between phases, but
   the *decoded* pixel content reaching the model must not.
5. Run `pytest backend/tests/ inference-sam3/tests/` and confirm no
   regressions on tests that were green at baseline.

## Cross-references

- Plan: `.claude/plans/the-prepared-raster-chips-effervescent-orbit.md`
- Research: `.claude/plans/the-prepared-raster-chips-effervescent-orbit-agent-ae36e99e23afbd293.md`
- Twin track (forward pass only): [sam3_comparison.md](sam3_comparison.md)
