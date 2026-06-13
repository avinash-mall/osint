# Imagery passes and FMV clips are deletable (rows + files + graph)

**Path:** [backend/routers/imagery.py](../../backend/routers/imagery.py) `delete_imagery`,
[backend/main.py](../../backend/main.py) `delete_fmv_clip`,
[frontend/src/components/map/LayerPanel.tsx](../../frontend/src/components/map/LayerPanel.tsx),
[frontend/src/components/FmvPlayer.tsx](../../frontend/src/components/FmvPlayer.tsx)
**Lines:** N/A (decision record spanning backend and frontend changes)
**Depends on:** `require_admin`, `db` (Neo4j) + `postgis_db`, `ConfirmDialog`

## Decision

Add admin-only hard deletes:

- `DELETE /api/imagery/{pass_id}` → delete `detections` for the pass + the
  `satellite_passes` row, remove the COG file, and `DETACH DELETE` the Neo4j
  `SatellitePass` + its `Detection` nodes.
- `DELETE /api/fmv/clips/{clip_id}` → delete `fmv_detections` + `fmv_frames` + the
  `fmv_clips` row, `rmtree` the clip's upload directory (video + HLS + sidecars), and
  `DETACH DELETE` the Neo4j `FmvClip` + `FmvDetection` nodes.

The frontend surfaces a trash button per imagery row (`data-tour="imagery-delete"`) and
per clip row (`data-tour="clip-delete"`), gated to admins and confirmed through the
shared `ConfirmDialog`. File and graph cleanup are best-effort so a half-missing
artifact still frees the row.

## Why this design

There was previously **no** way to remove an imagery pass or an FMV clip. Rows and their
multi-GB COG/video/HLS files accumulated forever, the live API smoke test could not
clean up what it ingested (so every run grew the corpus), and an analyst had no way to
purge a bad upload. Deletes mirror the existing PostGIS+Neo4j cascade in
`worker.clear_existing_detections`, so the two stores stay consistent. Hard delete (not
soft) is the right call: the intent is to reclaim disk and remove the artifact entirely,
unlike the per-detection soft-delete (`deleted_at`) which preserves review history.

## Orphan-free cascade (follow-up)

The original deletes relied on PostGIS FKs to cascade
`detection_target_candidates`, `detection_track_members`, and
`platform_identification_candidates`. Three data classes have **no FK** and were
left behind: `object_details` (analyst designation/threat, keyed by the
polymorphic string `(source, source_id)`), **empty `detection_tracks`** (a parent
track row survives after its last member cascades away), and
`operational_entity_tracks` links to those tracks. The single-detection
**soft**-delete additionally left its candidate links, track membership,
`object_details`, and Neo4j `:Detection` node fully live behind a hidden row.

All four delete paths now route their cleanup through
[backend/cascade_delete.py](../../backend/cascade_delete.py): `delete_imagery`,
`clear_existing_detections` (re-ingest/replace), `delete_fmv_clip`, and
`delete_detection`. The soft-delete keeps the `detections` row as an audit
tombstone (`deleted_at`) but purges its projections so nothing stale renders.
`object_details` is cleaned by `(source, source_id)` string match because it
cannot carry an FK. See [backend/cascade-delete.md](../backend/cascade-delete.md).

## Considered alternatives

- **Soft-delete (set a `deleted_at`).** Rejected: the goal is to reclaim disk and graph;
  a tombstoned row that still pins a multi-GB file defeats the purpose.
- **Backend routes only, no UI.** Rejected (per product decision): analysts need to
  purge bad uploads from the workspace, not just via curl.
- **Cascade via Postgres FKs / Neo4j triggers.** Rejected: the schema uses explicit
  application-level cascades elsewhere; matching that keeps the delete legible and
  avoids a migration.

## Cross-references

- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [backend/main-app-entrypoint.md](../backend/main-app-entrypoint.md)
- [scripts/smoke-test-live-api.md](../scripts/smoke-test-live-api.md) — uses these for teardown
- [frontend/product-tour.md](../frontend/product-tour.md) — `imagery-delete` tour anchor
