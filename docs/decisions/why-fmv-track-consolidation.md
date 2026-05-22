# Decision — Consolidate FMV tracks in a post-inference pass

## Context

`process_fmv` slices a drone-video clip into overlapping ~12 s windows, runs **one SAM3 `/detect_video` session per `(window, prompt)`**. SAM 3.1 multiplex resets tracker state on every text `add_prompt` → PCS mode *must* fan out one session per prompt. `_insert_detection_rows` then allocates `track_id`s keyed on `(window_idx, prompt_text, local_track_id)`.

Consequence: a single physical object gets a **different `track_id` in every window and every prompt** it appears in, and can carry several conflicting `class` labels. FmvPlayer side panel groups by `track_id` → one row per fragment; the list grows per frame, one object appears as several.

## Decision

Add `backend/fmv_tracker.py` — a **post-inference consolidation pass** run once after a clip finishes (the `worker.consolidate_fmv` Celery task, dispatched by `process_fmv`). Re-associates all `fmv_detections` of a clip into stable, clip-global tracks, votes one canonical class per track, soft-deletes cross-prompt per-frame duplicate rows.

### Why post-inference, not in-session

The fragmentation is caused by the upstream SAM 3.1 multiplex API resetting state per text prompt. Cannot be fixed inside one inference session. The only place with a full-clip, all-prompts view is *after* all windows land in `fmv_detections` → consolidation lives there.

### Why greedy-over-frames + Hungarian-per-frame + a merge pass

Chronological frame-to-frame association handles temporal continuity and bridges window seams. Per-frame `linear_sum_assignment` is optimal and 1:1 — but *because* it is 1:1 it can never merge the same object seen under two prompts at the *same* frames. A separate union-find **track-merge pass** over co-temporal spatially-coincident tracks closes that gap.

### Why class by temporal support, not peak confidence

The first instinct — keep the highest-confidence label — is wrong: a wrong label is typically a single-frame high-confidence spike, while a correct label persists. `_vote_class` ranks each label by the number of distinct frames it appears on (median confidence only breaks ties). Also stabilises YOLOE's per-frame label flicker.

### Open-vocabulary compliance (CLAUDE.md rule 5)

The pass **relabels and de-duplicates observations; it never narrows the open class set.** Class voting is a per-track relabel, not a taxonomy edit — no label removed from the ontology, no prompt list changes. Soft-deleted rows keep `original_class`/`original_track_id` in metadata and are recoverable (`deleted_at` is the project's existing reversible soft-delete). The class term in the association cost is *soft* (a 0.0/0.3/0.6 penalty, never `+inf`) → can only break ties, never forbid merging two differently-labelled detections of one object. No class is suppressed for being "noisy" — only *which of several simultaneously-claimed classes* describes one tracked object is resolved. No hard-coded class lists (rule 4).

### Removal of the `overlap_index` dedup

`_insert_detection_rows` previously carried an `overlap_index` that skipped window-seam duplicates within the same `(frame, class)` key. That is a strict subset of what the consolidation pass now does — and worse, it dropped second-window rows the consolidation pass wants as embedding anchors and seam-bridging evidence. Removed; consolidation is the single source of FMV de-duplication.

## Alternatives considered

- **Fix it in the frontend** (collapse groups by box proximity at render time): rejected — lossy, leaves noisy data in PostGIS for every other consumer.
- **Reuse `backend/tracker.py`** (the satellite multi-pass tracker): rejected — coupled to the `detections`/`satellite_passes` schema and geodesic coordinates. FMV detections are image-space cxcywh. Only its `_embedding_vector` helper is reused.
- **One SAM3 session for the whole clip:** impossible — multiplex loses targets within ~30 frames and resets per prompt.

## Cross-references

- [backend/fmv-track-consolidation.md](../backend/fmv-track-consolidation.md)
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [decisions/why-open-vocabulary.md](why-open-vocabulary.md)
- [decisions/why-yoloe-replaced-amg.md](why-yoloe-replaced-amg.md)
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
