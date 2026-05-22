# `backend/imagery_metadata.py` — Raster Metadata Extraction

**Path:** [backend/imagery_metadata.py](../../backend/imagery_metadata.py)
**Lines:** ~298
**Depends on:** `rasterio`, `hashlib`, `math`

## Purpose

Read GeoTIFF/NITF tags and derive: a SHA-256 of the file (for dedupe), the acquisition timestamp (NITF `IDATIM`, or any ISO-8601-shaped tag), and SAR-specific fields (incidence angle, look direction, polarization). Also exposes `native_max_zoom`, the WebMercatorQuad zoom that matches a COG's pixel resolution.

## Why this design

- **SHA-256 over disk content** is the dedupe key. Same raster uploaded twice should not create two satellite_passes rows. The hash is computed in 1 MiB chunks (no full-file read).
- **NITF `IDATIM` is preferred** over filename heuristics; ISO-8601 coercion is the next fallback. Filenames are unreliable.
- **SAR keys looked up by alias.** Different vendors emit `IPF_INC_ANGLE`, `INCIDENCE_ANGLE`, `INC_ANGLE`, etc.; the module tries all known aliases.
- **`native_max_zoom` derives GSD from `width`/`bounds`/`crs`** rather than calling TiTiler. Geographic CRSes get a cos-latitude metres conversion; projected CRSes use the bounds span directly. Result is clamped to `[10, 24]` and falls back to a `default` (18) on missing/degenerate tags so callers never see `None`. See [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md).

## Key symbols

- [`file_sha256`](../../backend/imagery_metadata.py#L58) — chunked SHA-256.
- [`_normalize_time`](../../backend/imagery_metadata.py#L69).
- [`parse_metadata_time`](../../backend/imagery_metadata.py#L108) — main timestamp resolver.
- [`_lookup_first`](../../backend/imagery_metadata.py#L123) — alias-aware lookup.
- [`_normalize_look_direction`](../../backend/imagery_metadata.py#L135).
- [`parse_sar_metadata`](../../backend/imagery_metadata.py#L155) — SAR-specific tag dict.
- [`native_max_zoom`](../../backend/imagery_metadata.py#L206) — WebMercatorQuad native-zoom ceiling from raster GSD.
- [`extract_raster_metadata`](../../backend/imagery_metadata.py#L252) — the public entry.

## Failure modes

- File unreadable → returns `{}`; ingest router rejects with 400.
- No timestamp tag → `parse_metadata_time` returns `None`; caller defaults to upload time.

## Cross-references

- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [architecture/data-flow-imagery.md](../architecture/data-flow-imagery.md)
- [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md)
