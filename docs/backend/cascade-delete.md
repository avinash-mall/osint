# `backend/cascade_delete.py` — Orphan-free delete helpers

**Path:** [backend/cascade_delete.py](../../backend/cascade_delete.py)
**Lines:** ~135
**Depends on:** `logging` only (operates on a caller-supplied PostGIS cursor + optional Neo4j session)

## Purpose

Shared application-level cleanup so every detection/imagery delete path leaves
**zero orphans**. PostGIS FKs already cascade `detection_target_candidates`,
`detection_track_members`, and `platform_identification_candidates` off a
deleted `detections` row, but three classes of data are unreachable by those
cascades and must be purged explicitly:

- **`object_details`** — analyst-asserted designation/threat/affiliation, keyed
  by the polymorphic string `(source, source_id)` (`'detection'` or
  `'fmv_detection'`); **no FK** to `detections`/`fmv_detections`.
- **Empty `detection_tracks`** — a parent track row survives after its last
  member is cascade-deleted, leaving a member-less track that still renders
  (the phantom-data class behind the cross-continent streak bug).
- **`operational_entity_tracks`** — analyst track attachments keyed by
  `track_id` with no FK; they dangle once the track row is gone.

Plus the Neo4j `:Detection` mirror node, which previously only the hard-delete
paths removed.

## Why this design

Explicit application-level cascades, not new FK migrations — matches the
established delete design ([why-deletable-imagery-and-clips.md](../decisions/why-deletable-imagery-and-clips.md)),
and `object_details` *cannot* take an FK anyway (its `source_id` is polymorphic
text spanning satellite and FMV detections). One module, reused by all four
delete paths, keeps the cleanup identical and legible everywhere.

## Key symbols

- [`affected_track_ids`](../../backend/cascade_delete.py#L35) — track ids that include any of the detection ids; **must be called before** the rows are deleted (the cascade removes the member rows).
- [`purge_object_details`](../../backend/cascade_delete.py#L52) — `DELETE FROM object_details WHERE source=%s AND source_id = ANY(%s)` (ids cast to text).
- [`purge_detection_children`](../../backend/cascade_delete.py#L66) — explicit delete of the FK-children a hard delete would cascade; needed on the **soft**-delete path where the row survives.
- [`purge_empty_tracks`](../../backend/cascade_delete.py#L83) — among the passed track ids, delete now-member-less tracks + their `operational_entity_tracks` rows. Scoped — never a global sweep.
- [`detach_delete_detection_nodes`](../../backend/cascade_delete.py#L110) — `DETACH DELETE` the Neo4j `:Detection` nodes.

## Inputs / Outputs

In: an open PostGIS cursor (`commit` managed by the caller) and id lists; Neo4j
session for the detach helper. Out: rows removed (PostGIS) / nodes detached
(Neo4j); helpers return counts where useful. All are no-ops on empty input.

## Failure modes

- Called after the `detections` rows are deleted → `affected_track_ids` returns
  `[]` (members already cascaded); callers must capture track ids first.
- Neo4j unavailable → the caller wraps `detach_delete_detection_nodes` in
  best-effort try/except so graph cleanup never fails the delete.

## Cross-references

- Callers: [imagery-router.md](../backend-routers/imagery-router.md) (`delete_imagery`),
  [detections-router.md](../backend-routers/detections-router.md) (`delete_detection` soft-delete),
  [main-app-entrypoint.md](main-app-entrypoint.md) (`delete_fmv_clip`),
  [worker-legacy-monolith.md](worker-legacy-monolith.md) (`clear_existing_detections`).
- [decisions/why-deletable-imagery-and-clips.md](../decisions/why-deletable-imagery-and-clips.md)
- Tests: [backend/tests/test_cascade_delete.py](../../backend/tests/test_cascade_delete.py)
