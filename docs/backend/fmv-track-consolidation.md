# `backend/fmv_tracker.py` — FMV Track Consolidation

**Path:** [backend/fmv_tracker.py](../../backend/fmv_tracker.py)
**Lines:** ~512
**Depends on:** `numpy`, `scipy.optimize.linear_sum_assignment`, [backend/geometry.py](geometry-iou.md), [backend/tracker.py](tracker-satellite.md) (`_embedding_vector`), [backend/ontology.py](ontology-system.md) (`normalize`), env `FMV_TRACKER_COST_WEIGHTS`

## Purpose

Post-inference pass re-associating every `fmv_detections` row of a drone-video clip into stable **clip-global** tracks. `process_fmv` slices a clip into overlapping windows, runs one SAM3 `/detect_video` session per `(window, prompt)` → identity breaks at every window seam + every prompt; one object accumulates dozens of `metadata.track_id` values + several conflicting `class` labels. This module collapses that into one track + one class per object.

Entry: `consolidate_fmv_tracks(clip_id, *, postgis_db)`; runs as `worker.consolidate_fmv` Celery task (dispatched by `process_fmv` on completion, [worker-legacy-monolith.md](worker-legacy-monolith.md)).

## Why this design

- **Post-inference, not in-session** — SAM 3.1 multiplex resets state on every text `add_prompt` → multi-prompt identity unfixable inside one session; reconciled afterwards over the full clip. See [decisions/why-fmv-track-consolidation.md](../decisions/why-fmv-track-consolidation.md).
- **Greedy over frames, Hungarian within a frame** — chronological streaming association (`_associate_spatial`) handles temporal continuity; per-frame `linear_sum_assignment` gives optimal 1:1 match.
- **Track-merge pass for co-temporal duplicates** — per-frame assignment is 1:1, never merges the same object seen under two prompts at the *same* frames. `_merge_cotemporal` union-finds tracks whose boxes coincide on shared frames.
- **Class by temporal support, not peak confidence** — `_vote_class` ranks a label by distinct frame count (median confidence breaks ties); a one-frame high-confidence misfire loses to a persistent label. Also stabilises YOLOE per-frame label flicker.
- **Soft-delete, not hard-delete** — cross-prompt per-`(track, frame)` duplicates retired via `deleted_at`; losing rows keep `original_class`/`original_track_id` in metadata, recoverable. Pass relabels/de-duplicates *observations* — never narrows the open class set ([decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)).
- **Idempotent** — loads only `deleted_at IS NULL` rows; consolidated ids numbered deterministically by earliest member frame/id → re-run reproduces the mapping, collapses nothing further.

## Key symbols

- [`consolidate_fmv_tracks`](../../backend/fmv_tracker.py#L435) — DB entry; loads rows, runs `consolidate`, writes rewrites + soft-deletes in one commit block.
- [`consolidate`](../../backend/fmv_tracker.py#L311) — pure core (no DB); returns rewrite/soft-delete plan. Unit-testable.
- [`_associate_spatial`](../../backend/fmv_tracker.py#L204) — frame-to-frame association (Hungarian per frame).
- [`_merge_cotemporal`](../../backend/fmv_tracker.py#L242) — union-find merge of co-temporal coincident tracks (cross-prompt dedup).
- [`_pair_cost`](../../backend/fmv_tracker.py#L161) — IoU + embedding + temporal-gap + soft class-penalty cost, with hard gates.
- [`_vote_class`](../../backend/fmv_tracker.py#L293) — temporal-support canonical-class vote.
- [`_load_weights`](../../backend/fmv_tracker.py#L61) — `FMV_TRACKER_COST_WEIGHTS` env JSON.

## Inputs / Outputs

- **Input:** `fmv_detections` rows for a clip (`bbox` cxcywh-normalised, `metadata.track_id`/`embedding`); clip `fps` from `fmv_clips`.
- **Output:** in-place — surviving rows get `class` ← voted canonical, `metadata.track_id` ← consolidated id (1-based; `0` falsy in UI), `metadata.consolidated/original_class/original_track_id/consolidation_run_at`; duplicate rows get `deleted_at`. Returns stats dict (`input_rows`, `consolidated_tracks`, `rows_soft_deleted`, `rows_rewritten`, `heartbeat_rows`, `class_changes`).

## Failure modes

- Empty clip → early zero-result return, no writes.
- `tracker._embedding_vector` / `ontology.normalize` unimportable → embedding term degrades to neutral 0.5, class penalty falls back to lowercased label; association still works on geometry.
- Consolidation exception inside `worker.consolidate_fmv` → logged, raw detections left in place (fragmented but usable).
- Heartbeat rows (`bbox == []`) carry no geometry → routed to their lineage's track, never soft-deleted.

## Cross-references

- [worker-legacy-monolith.md](worker-legacy-monolith.md) — `process_fmv` hook + `worker.consolidate_fmv` task
- [decisions/why-fmv-track-consolidation.md](../decisions/why-fmv-track-consolidation.md)
- [tracker-satellite.md](tracker-satellite.md) — the satellite-pass tracker (separate module)
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
- [testing/backend-unit-tests.md](../testing/backend-unit-tests.md) — `test_fmv_tracker.py`
