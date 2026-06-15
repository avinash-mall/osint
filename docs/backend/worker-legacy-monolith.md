# `backend/worker_legacy.py` — Monolithic Celery Tasks

**Path:** [backend/worker_legacy.py](../../backend/worker_legacy.py)
**Lines:** ~6193 (largest file in the repo)
**Depends on:** Most of the rest of `backend/` plus `celery`, `requests`, `numpy`, `rasterio`, `cv2`, `ipaddress`, `socket`, env `ALLOW_REMOTE_IMAGERY_URLS`, `REMOTE_IMAGERY_ALLOWED_HOSTS`, `REMOTE_IMAGERY_MAX_BYTES`

## Purpose

Every heavy-lifting Celery task: imagery pipeline, FMV pipeline, training-job orchestration, audio transcription, shared helpers (chip planner, SAM3 HTTP client).

## Why this file is monolithic

See [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md). Celery task names are routing identity; refactoring is gated on preserving the `name=` argument and adding test coverage per extracted piece.

## Key task names (by `name=` argument)

| Task name | Purpose |
|---|---|
| `worker.process_satellite_imagery` | Imagery ingest: COG → chip → /detect → georef → persist |
| `worker.process_fmv` | FMV ingest: HLS → KLV → /detect_video → persist raw tracks; dispatches `worker.consolidate_fmv` on completion |
| `worker.consolidate_fmv` | Post-inference FMV track consolidation over `fmv_detections` (`default` queue) — see [fmv-track-consolidation.md](fmv-track-consolidation.md). Also dispatches `worker.project_fmv_to_graph` per Phase 2.B. |
| `worker.train_model` | Forward training request to `inference-sam3:/train`, persist results |
| `worker.transcribe_audio` | (When enabled) audio → text |
| `worker.tick_feed_poll` | Periodic feed polling (Celery beat) |
| `worker.cleanup_old_observations` | Hourly retention sweep over `observations` + `timeline_events` (`OBSERVATION_RETENTION_DAYS`, default 30 d) |
| `worker.seed_reference_db` | Bake reference platforms/chips from `/opt/reference-corpora/`; the `force=False` skip path still publishes a terminal `done` (`skipped: true`) WS event so the admin Seed button resolves |

**Link Graph projectors** (Phases 2-3): mirror PostGIS rows into Neo4j identity nodes. See [conventions/adding-a-new-graph-projector.md](../conventions/adding-a-new-graph-projector.md).

| Task name | Purpose |
|---|---|
| `worker.project_fmv_to_graph` | Phase 2.B — FMVClip + per-track FMVDetection nodes. Dispatched on consolidate completion. |
| `worker.project_documents_to_graph` | Phase 2.C — :Document stub + :MENTIONS edges from `documents.extracted_entities`. Triggered after extraction; skips when extracted list empty (Phase 5.A). |
| `worker.project_observations_to_graph` | Phase 2.D — :Observation + :OBSERVED_AT bridges for `observations` rows with `entity_id`. Single-row or full backfill mode. |
| `worker.project_ontology_to_graph` | Phase 3.A — OntologyBranch + OntologyObject mirror; triggered on `bump_version`. |
| `worker.project_unknown_labels` | Phase 3.A — :UnknownLabel mirror + SUGGESTED_BRANCH + LABEL_OF orbits; on-write hook in `_log_unknown`. |
| `worker.project_label_of_edges` | Phase 3.C — `(d:Detection)-[:LABEL_OF]->(o:OntologyObject)` batch projector. |

**Link Graph beat tasks** (Phase 4-5): periodic maintenance of derived edges + LLM-assisted proposals.

| Task name | Default cadence | Purpose |
|---|---|---|
| `worker.tick_near_builder` | 60 min | Phase 4.C — :NEAR edges from Detection → Base/LaunchPoint/Facility via incremental ST_DWithin. Reads per-kind radius from `repeat_detector_thresholds` (Phase 5.B) with env fallback. |
| `worker.tick_repeat_detector` | 24 h | Phase 4.D — representative :REPEATED_AT edges per class+site cluster. Thresholds from `repeat_detector_thresholds` with env fallback. |
| `worker.tick_entity_resimilarity` | 7 d | Phase 4.E + 5.J + 5.K — POSSIBLY_SAME_AS candidate edges. Embedding cosine branch (when both entities have `re_id_embedding`) + name-match heuristic fallback. Time + AOI scoped. |
| `worker.tick_propose_entities` | 24 h | Phase 4.F + 5.I — `entity_candidates` rows from REPEATED_AT clusters. LLM-first (via [ai.py](../../backend/ai.py)), heuristic fallback. Calls `get_llm_json(prompt=..., system=...)` with the client-owned zero-temperature JSON path. |
| `worker.tick_aggregate_entity_embeddings` | 12 h | Phase 5.J — average `detection_tracks.embedding_anchor` per entity into `operational_entities.re_id_embedding` centroid. |
| [`worker.tick_colocation_builder`](../../backend/worker_legacy.py#L3719-L3771) | 6 h | Phase 6 — MERGE `COLOCATED_WITH` proximity edges between recent detection centroids (kNN/fixed-radius graph from [graph_proximity.py](graph-proximity.md), vendored from city2graph). Idempotent MERGE, no per-site cursor. PostGIS read + Neo4j write only. `queue=default`. See [decisions/why-proximity-colocation-graph.md](../decisions/why-proximity-colocation-graph.md). |
| [`worker.tick_gnn_link_prediction`](../../backend/worker_legacy.py#L3774-L3846) | 24 h | Phase 6 — GraphSAGE link prediction over the non-candidate entity graph; MERGEs the top predicted links as advisory `GNN_SUGGESTED_LINK` edges. **No-ops cleanly (`{"skipped": "torch_unavailable"}`) until torch is installed** — optional infra, like DEM/OSRM. `queue=default`. See [decisions/why-gnn-link-prediction.md](../decisions/why-gnn-link-prediction.md). |

`grep -nE "@celery_app.task" backend/worker_legacy.py` for the full live list (≈27 tasks total as of Phase 6).

## Key shared helpers (referenced from elsewhere)

- `chip_to_uint8_rgb` — multispectral chip → 1008×1008 uint8 RGB SAM3 wants.
- [`slice_and_infer` chip-pass planner](../../backend/worker_legacy.py#L1802-L1934) — builds `chip_passes`: the main grid plus a **small-object** second pass at `INFERENCE_SMALL_OBJECT_CHIP_SIZE` (finer chip → more pixels-per-object on small targets; default **504/on** as of [decisions/dense-scene-recall-defaults.md](../decisions/dense-scene-recall-defaults.md), `0` disables; [#L1808-L1817](../../backend/worker_legacy.py#L1808-L1817)) and an opt-in single **full-scene** pass (`INFERENCE_FULL_SCENE_PASS`, [#L1859-L1886](../../backend/worker_legacy.py#L1859-L1886)) over the whole image read decimated from COG overviews to catch objects larger than one chip. All passes share the dedupe index (NMS/WBF suppresses cross-scale/whole-vs-fragmented duplicates); the full-scene pass adds exactly 1 window to the progress total and is excluded from grid-coverage aggregates. Per-chip `scale_x`/`scale_y` ([#L2012-L2093](../../backend/worker_legacy.py#L2012-L2093)) map chip-pixel boxes back to source pixels before the affine warp — `1.0` for normal chips, `>1` only for the decimated full-scene chip. See [decisions/multi-scale-and-full-scene-chip-passes.md](../decisions/multi-scale-and-full-scene-chip-passes.md).
- `plan_inference_grid(...)` — slice a COG into chip windows with overlap. Block-snapped origins (tiled COGs) return `x_offsets`/`y_offsets` **plus `x_window_sizes`/`y_window_sizes`**: snapping an origin down extends that window by the snap delta, so the grid keeps full pixel coverage (the old snap left recurring ~16 px never-analyzed strips at chip 1008 / overlap 252 / block 512 — regression test in [tests/test_plan_inference_grid.py](../../backend/tests/test_plan_inference_grid.py)).
- `_emit_chip_payload(..., modality_hint=...)` — encodes one chip for upload. The operator/pipeline modality from ingest metadata is authoritative: it forces the SAR GeoTIFF branch for single-band GRDs / stripped band descriptions that fail the VV/VH heuristic (those chips previously shipped as `modality=rgb` and took the optical path).
- `_rederive_geo_from_pixel_bbox` + `_geo_stale_after_merge` — refresh `pixel_obb`/`geo_polygon`/`geo_bbox` after WBF fusion or edge reconciliation mutates `pixel_bbox`, so persisted geometry matches the merged box ([tests/test_dedupe_geo_rederive.py](../../backend/tests/test_dedupe_geo_rederive.py)).
- [`_remote_imagery_allowed`](../../backend/worker_legacy.py#L502-L528) — validates remote imagery hosts before worker-side HTTP(S) fetch.
- [`resolve_input_path`](../../backend/worker_legacy.py#L531-L569) — resolves staged local paths and gated remote URLs into an input path.
- SAM3 HTTP client constants (`INFERENCE_SAM3_URL`, timeouts, `INFERENCE_RESTART_RETRY_MAX`, `INFERENCE_RESTART_WAIT_S`, `INFERENCE_MAX_FAILED_CHIP_FRACTION`).
- `_inference_unavailable` / `_wait_for_inference_healthy` / `_post_chip_with_restart_retry` — classify whole-service unavailability vs per-chip errors, wait for `model_loaded`, and retry a chip POST across an inference self-heal restart. Wrap both `_post_chip_to_sam3` (multipart `/detect`) and `_post_chip_to_sam3_raw` (raw `/detect_raw`). See [decisions/why-retry-chips-across-inference-restart.md](../decisions/why-retry-chips-across-inference-restart.md).
- NDJSON consumer for `/detect_video` (parses streaming response, yields per-frame records).
- [`_calibration_tag_for_detection`](../../backend/worker_legacy.py#L662-L664) — chooses `source_layer` for detector-specific calibration.
- [`_llm_propose_entities`](../../backend/worker_legacy.py#L3429-L3487) — schema-constrained LLM proposer over REPEATED_AT clusters; raises/falls back cleanly.
- [`store_detections`](../../backend/worker_legacy.py#L2647-L2880) — persists calibrated, georeferenced, evidence-ranked detections, and **back-fills the returned `id`/`pass_id` onto each in-memory dict** so callers (the live-streaming `_store_chip` below) reference the row without a re-query. Plan C: immediately after each `INSERT INTO detections … RETURNING id`, opens a `SAVEPOINT auto_identify` and calls [`attach_identification_candidates`](reference-platform-db.md) with the row's `embedding_anchor` (best-effort: any exception is logged at WARNING and the savepoint is rolled back so a helper failure cannot poison the surrounding batch transaction — see [why-auto-identify-in-backend-not-inference.md](../decisions/why-auto-identify-in-backend-not-inference.md)). Top-1 score ≥ `REFERENCE_ID_AUTO_THRESHOLD` (default `0.85`, env-overridable) auto-applies `platform_*` to `object_details` per [why-auto-write-with-threshold.md](../decisions/why-auto-write-with-threshold.md). Task 1.2: also calls [`display_label_for`](detection-policy.md) and persists `display_label` + `label_quality` advisory metadata fields so the UI can render generic DOTA-OBB detections as `"Aircraft (generic)"` instead of a fabricated specific defence label — see [decisions/why-generic-labels-when-unverified.md](../decisions/why-generic-labels-when-unverified.md). The per-detection Neo4j `:Detection` projection (`CREATE` + `CONTAINS_DETECTION`) is wrapped best-effort in `try/except`: it runs inside the committing PostGIS cursor, so an un-caught Neo4j error would otherwise roll back the whole chip's detection INSERTs — the mirror is non-authoritative, so a graph blip must not lose source-of-truth detections.
- [`_store_chip`](../../backend/worker_legacy.py#L5003) + [`_det_to_live_feature`](../../backend/worker_legacy.py#L197) — **live (streaming) detections.** The per-chip `on_chip_store` callback persists the chip's detections then publishes a `detections_partial` WS event on the `detections` topic. When `LIVE_DETECTIONS_STREAM=1` (default) the event embeds a compact map-ready GeoJSON `features` array (built from the back-filled dicts) so the map renders detections as each chip completes (~7 s in), not after the ~90 s pass; a chip above `LIVE_DETECTIONS_MAX_FEATURES` streams counts only. The end-of-pass `detections_updated` event triggers the frontend's authoritative reload (reconciliation). See [decisions/why-live-streaming-detections.md](../decisions/why-live-streaming-detections.md).
- [`FMV_DEFAULT_PROMPTS`](../../backend/worker_legacy.py#L236) — PCS fallback prompt set (`vehicle,person,building`) when operator gave no FMV prompts.
- [`generate_candidate_links_for_pass`](../../backend/worker_legacy.py#L4553) — scores each detection in a pass against Neo4j `:Target`s and writes pending `detection_target_candidates` rows + their graph edges. Returns early when there are **no targets** (the common case — no candidates are possible). The per-target `history_anchor` is resolved **once** into a dict rather than re-queried inside the per-detection loop, and the Neo4j edges are accumulated and written via [`merge_candidate_detected_as_batch`](graph-writes.md) in 1000-row chunks **after** the PostGIS commit (one `session.run` per candidate previously stalled dense scenes). Pure scoring stays in [candidate-linking.md](candidate-linking.md).

## Fork safety

Import can issue DB queries in the Celery MainProcess → that builds `postgis_db`'s connection pool before the prefork pool forks workers. (The former import-time `DETECTION_POLICY = active_detection_policy()` global is gone — the policy is now fetched per chip-batch/store call so admin confidence-override changes reach the long-lived worker; see [decisions/audit-fixes-worker-2026-06-11.md](../decisions/audit-fixes-worker-2026-06-11.md).) A `worker_process_init` handler (`_reset_db_pool_after_fork`, just after the `celery_app` definition) calls `postgis_db.reset_after_fork()` in every child → each rebuilds its own pool. Without it the first task per child fails with `DatabaseError: error with status PGRES_TUPLES_OK and no message from the libpq`. See [decisions/reset-db-pool-after-fork.md](../decisions/reset-db-pool-after-fork.md).

## Inputs / Outputs

Imagery tasks emit per-pass summaries with `candidates_by_layer`, `suppressed_by_nms`, `suppressed_by_policy` from inference debug counts. Imagery pipeline calibrates raw confidence by `source_layer`, applies [detection-policy.md](detection-policy.md), georeferences OBBs, deduplicates across chips, applies [detection-evidence.md](detection-evidence.md), persists survivors to PostGIS. Still-image YOLOE was removed; imagery stays on the SAM3 sensor pipeline plus gated specialists.

**Pass-level dedup is content-hash-only.** Before cataloging, `process_satellite_imagery` looks for an existing `satellite_passes` row with the **same `source_hash`** (SHA-256 of the file) and, if found, UPDATEs it in place (`replacement=True`) — so re-processing a byte-identical raster is idempotent. The match is hash-only: the former `acquisition_time + footprint + source_filename/name` heuristics collapsed *distinct* uploads that merely shared a timestamp and footprint into one row, silently dropping the second image. See [decisions/why-imagery-dedup-is-hash-only.md](../decisions/why-imagery-dedup-is-hash-only.md) and [imagery-metadata-hashing.md](imagery-metadata-hashing.md).

`process_satellite_imagery(image_url, ...)` accepts staged local paths by default. HTTP(S) `image_url` inputs are rejected unless remote ingestion is explicitly enabled and the host passes allowlist/IP checks.

FMV tasks consume `/detect_video` NDJSON. SAM3 + YOLOE entries preserve `source_layer` in row metadata → downstream review distinguishes tracker families. `_insert_detection_rows` writes rows **raw** — window-seam + cross-prompt duplicates included; identity reconciled afterwards by `worker.consolidate_fmv` ([fmv-track-consolidation.md](fmv-track-consolidation.md)), which `process_fmv` dispatches once all windows finish. The earlier per-`(frame, class)` `overlap_index` dedup was removed — see [decisions/why-fmv-track-consolidation.md](../decisions/why-fmv-track-consolidation.md).

## Failure modes

- **FMV leaves the GPU on `fmv`, then reverts.** `process_fmv` loads the `fmv` profile (`_ensure_fmv_profile`) and, in its `finally`, calls `_revert_inference_profile(session, "imagery_rgb")` — best-effort, 409-tolerant — so the COP's imagery detection isn't left degraded after a clip is processed (reverts to the light RGB profile, not the full union, so tight-VRAM cards don't OOM). A 409 (another FMV session live) correctly keeps `fmv`. See [decisions/why-revert-inference-after-fmv.md](../decisions/why-revert-inference-after-fmv.md).
- `/detect` per-chip error (`ReadTimeout`, a 500 on one tile, bad JSON) → increments failed chip counts; worker continues other chips. **Whole-service unavailability is different**: `_inference_unavailable` classifies `ConnectionError`/`ChunkedEncodingError` + HTTP 502/503/504 as "inference is down/restarting/preloading" and `_post_chip_with_restart_retry` waits for `/health` to report `model_loaded` then retries the chip (up to `INFERENCE_RESTART_RETRY_MAX`×`INFERENCE_RESTART_WAIT_S`), so a CUDA self-heal restart (~100-150 s) is ridden out instead of dropping the rest of the scene. See [decisions/why-retry-chips-across-inference-restart.md](../decisions/why-retry-chips-across-inference-restart.md).
- **The per-pass guard fails the job past a failure-fraction tolerance, not only when *every* chip failed.** After retries, if `failed_chips/processed_chips > INFERENCE_MAX_FAILED_CHIP_FRACTION` (default 0.05) or all chips failed, `process_satellite_imagery` raises → the upload finishes `status='failed'` (with the error on the job) instead of finalizing `ready` with a misleading near-zero result. `inference_success_fraction` is added to the summary. Fixes the old false-success where a mid-job restart left `failed_chips=222, coverage_fraction=1.0, state=success`. See [decisions/why-retry-chips-across-inference-restart.md](../decisions/why-retry-chips-across-inference-restart.md) and [decisions/why-dynamic-modality-loading-on-tight-vram.md](../decisions/why-dynamic-modality-loading-on-tight-vram.md).
- Remote HTTP(S) imagery URL while `ALLOW_REMOTE_IMAGERY_URLS=0`, not allowlisted, resolving to private/link-local/multicast/reserved IPs, or exceeding `REMOTE_IMAGERY_MAX_BYTES` → task fails before chip processing and removes partial remote downloads.
- Detections below the active policy floor → counted in `suppressed_by_policy`, not persisted.
- Evidence ranking never drops detections; weak rows persisted as `candidate`/`discovery` metadata.
- Missing FMV prompts no longer launch a single `"object"` session; precision fallback launches the bounded `FMV_DEFAULT_PROMPTS` list.
- LLM entity proposal failures (unset endpoint, transport error, malformed JSON, or empty valid proposal list) fall back to the deterministic REPEATED_AT heuristic.

## Re-export shape

Everything here is re-exported by [backend/worker/__init__.py](../../backend/worker/__init__.py) so callers can `from worker import process_fmv`. New code should prefer `from worker.imagery import ...` via the [worker package facade](worker-package-facade.md).

## Cross-references

- [backend/worker-package-facade.md](worker-package-facade.md)
- [backend/cascade-delete.md](cascade-delete.md) — `clear_existing_detections` (re-ingest/replace) routes its cleanup here so a replaced pass leaves no `object_details` / empty-track orphans
- [backend/detection-evidence.md](detection-evidence.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
- [decisions/multi-scale-and-full-scene-chip-passes.md](../decisions/multi-scale-and-full-scene-chip-passes.md)
- [decisions/dense-scene-recall-defaults.md](../decisions/dense-scene-recall-defaults.md) — small-object pass default flipped on (504)
- [benchmarks/chip-dedupe-nms-vs-wbf-2026-06-12.md](../benchmarks/chip-dedupe-nms-vs-wbf-2026-06-12.md) — cross-chip dedupe NMS-vs-WBF eval; verdict: keep `DEDUPE_METHOD=nms` default
- [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md)
- [decisions/reset-db-pool-after-fork.md](../decisions/reset-db-pool-after-fork.md)
- [backend/database-connections.md](database-connections.md)
- [decisions/why-evidence-ranked-detections.md](../decisions/why-evidence-ranked-detections.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [decisions/why-generic-labels-when-unverified.md](../decisions/why-generic-labels-when-unverified.md)
- [operations/celery-queues-and-tasks.md](../operations/celery-queues-and-tasks.md)
- [operations/celery-beat-schedule.md](../operations/celery-beat-schedule.md)
- [conventions/adding-a-new-celery-task.md](../conventions/adding-a-new-celery-task.md)
- [decisions/why-security-hardening-2026-05-31.md](../decisions/why-security-hardening-2026-05-31.md)
- [backend/graph-proximity.md](graph-proximity.md), [backend/graph-pyg.md](graph-pyg.md) — Phase 6 builders
- [decisions/why-proximity-colocation-graph.md](../decisions/why-proximity-colocation-graph.md), [decisions/why-gnn-link-prediction.md](../decisions/why-gnn-link-prediction.md)
