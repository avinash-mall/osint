# Imagery pass dedup is content-hash-only

**Path:** [backend/worker_legacy.py](../../backend/worker_legacy.py) `process_satellite_imagery` (the `satellite_passes` SELECT-then-UPDATE-or-INSERT block)
**Lines:** ~30
**Depends on:** `source_hash` from [imagery_metadata.py](../../backend/imagery_metadata.py), `satellite_passes` (`file_path` UNIQUE)

## Decision

When cataloging an imagery upload, dedup against existing `satellite_passes`
rows on **`source_hash` (SHA-256 of file content) alone**:

```sql
SELECT id FROM satellite_passes
WHERE %s IS NOT NULL AND source_hash = %s
ORDER BY updated_at DESC NULLS LAST, created_at DESC
LIMIT 1
```

A match → UPDATE that row in place (`replacement=True`). No match (or no hash) →
INSERT a new pass (`replacement=False`).

## Why this design

The previous query matched on `acquisition_time = X AND (source_hash = H OR
(source_filename = F AND ST_Equals(footprint)) OR (name ~ F AND
ST_Equals(footprint)))`. The two footprint-based branches collapsed
**genuinely distinct uploads** onto one row whenever they happened to share an
`acquisition_time` and footprint — e.g. two scenes from the same satellite
pass, or two crops of one mosaic, or two files an analyst named the same. The
UPDATE overwrote the first pass's `file_path`/footprint/metadata, so the second
image was **processed but never appeared** as its own pass (and the first's COG
orphaned on disk).

A SHA-256 collision means the files are byte-identical — the *same raster*. That
is the only case where collapsing two uploads is correct, and it is exactly what
[imagery-metadata-hashing.md](../backend/imagery-metadata-hashing.md) already
documents the hash for ("same raster uploaded twice must not create two rows").
Hash equality already implies identical content, so the `acquisition_time` gate
is redundant and was dropped too — a re-upload of a raster with no embedded
acquisition time (so `acq_time` defaults to `now()`, which differs per upload)
now still dedups instead of creating a duplicate.

`cog_path` is unique per upload (it is prefixed with the `upload_id`), so a
distinct upload falling through to INSERT never collides on the `file_path`
UNIQUE constraint.

## Considered alternatives

- **Keep the footprint/filename branches but also require `source_hash`
  equality.** Pointless: once `source_hash` matches, the files are identical, so
  the extra predicates can only narrow a set that is already "the same file."
- **Dedup on footprint + acquisition_time (drop hash).** Rejected — this *is* the
  bug: two different scenes from one pass share both and would still collapse.
- **Add a UNIQUE(source_hash) constraint instead of a SELECT.** Rejected:
  `source_hash` is nullable (metadata extraction can fail), and the worker needs
  the existing `id` to UPDATE in place + re-run detections, not just a conflict
  rejection.

## Cross-references

- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [backend/imagery-metadata-hashing.md](../backend/imagery-metadata-hashing.md)
- [decisions/why-deletable-imagery-and-clips.md](why-deletable-imagery-and-clips.md)
