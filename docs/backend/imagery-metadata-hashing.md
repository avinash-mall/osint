# `backend/imagery_metadata.py` — Raster Metadata Extraction

**Path:** [backend/imagery_metadata.py](../../backend/imagery_metadata.py)
**Lines:** ~298
**Depends on:** `rasterio`, `hashlib`, `math`

## Purpose

Read GeoTIFF/NITF tags; derive: file SHA-256 (dedupe), acquisition timestamp (NITF `IDATIM`, or any ISO-8601-shaped tag), SAR fields (incidence angle, look direction, polarization). Also exposes `native_max_zoom`, the WebMercatorQuad zoom matching a COG's pixel resolution.

## Why this design

- **SHA-256 over disk content = dedupe key** — same raster uploaded twice must not create two `satellite_passes` rows. Computed in 1 MiB chunks (no full-file read). The `satellite_passes` catalog dedups on this hash **alone** (a byte-identical re-upload replaces the existing pass; anything else is a new pass) — see [decisions/why-imagery-dedup-is-hash-only.md](../decisions/why-imagery-dedup-is-hash-only.md).
- **NITF `IDATIM` preferred** over filename heuristics; ISO-8601 coercion is next fallback. Filenames unreliable.
- **SAR keys looked up by alias** — vendors emit `IPF_INC_ANGLE`, `INCIDENCE_ANGLE`, `INC_ANGLE`, etc.; module tries all known aliases.
- **`native_max_zoom` derives GSD from `width`/`bounds`/`crs`**, not TiTiler. Geographic CRSes get cos-latitude metres conversion; projected CRSes use bounds span directly. Clamped `[10, 24]`, falls back to `default` (18) on missing/degenerate tags → callers never see `None`. See [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md).

## Key symbols

- [`file_sha256`](../../backend/imagery_metadata.py#L58) — chunked SHA-256.
- [`_normalize_time`](../../backend/imagery_metadata.py#L69).
- [`parse_metadata_time`](../../backend/imagery_metadata.py#L108) — main timestamp resolver.
- [`_lookup_first`](../../backend/imagery_metadata.py#L123) — alias-aware lookup.
- [`_normalize_look_direction`](../../backend/imagery_metadata.py#L135).
- [`parse_sar_metadata`](../../backend/imagery_metadata.py#L155) — SAR-specific tag dict.
- [`native_max_zoom`](../../backend/imagery_metadata.py#L206) — WebMercatorQuad native-zoom ceiling from raster GSD.
- [`extract_raster_metadata`](../../backend/imagery_metadata.py#L252) — public entry.

## Failure modes

- File unreadable → `{}`; ingest router rejects with 400.
- No timestamp tag → `parse_metadata_time` returns `None`; caller defaults to upload time.

## Cross-references

- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)
- [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md)
