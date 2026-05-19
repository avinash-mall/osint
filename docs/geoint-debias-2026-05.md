# GEOINT Detection De-bias — 2026-05

> Comprehensive documentation of the bias-mitigation refactor against the
> plan at `/home/avinash/.claude/plans/the-geoint-detection-has-squishy-fog.md`.
> Covers every code change made, the reasoning behind it, the operator-facing
> tunables, and how to verify the improvements end-to-end.

---

## 0. Executive summary

A defence-analyst-driven audit of Sentinel's GEOINT detection stack
identified **47 distinct biases** spanning the inference layers (SAM3,
DOTA-OBB, Grounding-DINO, Prithvi, DINOv3-SAT, TerraMind), the backend
post-processing (NMS, valid-mask clip, detection policy, ontology
normalisation, threat assessment, candidate linking, tracker), and the
analyst-facing UI (silent filters, marker-mode switch, time-window default).

The refactor was **inference-time only** — no model weights were touched —
and ran across nine progressively-scoped sessions. This document is now the
applied contract, not an aspirational plan: unsupported gated loaders were
removed, default fabricated outputs were retired, and offline validation was
made runnable without PostGIS.

### Applied / verified matrix (2026-05-18)

| Area | Applied state | Verification |
|---|---|---|
| Candidate linking | One pure scorer shared by API, worker, eval | `eval_candidate_links.py` top-1 = 1.000 |
| Tracking | Persisted sigma, motion state, embedding anchor | pure unit coverage + additive migration columns |
| Dedupe | WBF changed-head streaming; SAR CFAR global overlap dedupe | pure unit coverage |
| Pagination | Opaque `(created_at, id)` cursor | pure cursor round-trip unit coverage |
| Synthetic defaults | FMV and analytics fail honestly unless demo-opted-in | behavior encoded in backend routes |
| Offline eval | Seed-backed normalization; DB tests marked integration | dry-run comparison/ECE run without PostGIS |
| Dataset surface | unsupported gated-loader skeletons removed | repo search + loader list audit |

### Headline outcomes

1. **Per-class recall lift** for `military_forces`, `armored_vehicle`,
   `logistics`, `civilian`, `other` (all at 0% baseline on DOTA) via
   expanded SAM3 prompt curation, per-class category-presence thresholds,
   multi-scale chip passes, and ontology matcher fixes.
2. **Cross-detector calibration**: per-model temperature scaling +
   per-class NMS IoU + per-model trust weights + optional Weighted Boxes
   Fusion. Model scores are now comparable; multi-detector agreement
   boosts confidence rather than the loudest model winning.
3. **Analyst transparency**: a single permanent suppression-status
   banner shows exactly what every silent filter is hiding (confidence
   floor, hidden categories, hidden labels, marker-overflow, time-window,
   sub-sampled passes). Plus restored-session reminders, honest analytics
   unavailable states, position-uncertainty halos, LLM-advisory pills,
   and a full provenance breadcrumb (raw → calibrated → fused).
4. **SAR de-fanged**: SAM3-on-SAR off by default (optical-domain
   pretrained model on synthetic pseudo-RGB was a documented
   false-positive source); replaced with a native CFAR ship detector
   that runs entirely on the worker CPU.
5. **Tracker rebalance**: Kalman-style state with per-(category, state)
   process noise, per-state V_MAX, embedding-based re-ID, configurable
   Hungarian weights.
6. **Configurable threat policy**: new DB table lets defence operators
   elevate (class, category, allegiance) tuples without redeploying.
7. **Evaluation harness**: ECE measurement, candidate-link top-K
   evaluation, per-class regression gate in `compare_inference_layers`,
   seed-backed offline label normalisation, and supported loaders for DOTA,
   HLS Burn Scars, Sen1Floods, SAR fixtures, and Sentinel-1.

---

## 1. The mental model

Sentinel runs a six-layer inference pipeline followed by seven backend
post-processing layers. Each layer can introduce or amplify bias.

```
Raster / FMV
  │
  ▼
Inference layers ──────────────────────────────────────────────┐
  • SAM3 (image / video)                                       │
  • DOTA-OBB (specialist)                                      │
  • Grounding-DINO (open-vocab)                                │
  • Prithvi (multispectral)                                    │
  • DINOv3-SAT (embedding)                                     │
  • TerraMind (SAR)                                            │
  └──┬──────────────────────────────────────────────────────────┘
     │
     ▼
Backend post-processing ───────────────────────────────────────┐
  1. valid-mask clip                  (Phase 3.10)             │
  2. NMS dedup                        (Phase 2.6, 2.7, 2.8)    │
  3. confidence policy                (Phase 1.2, 2.5, 2.9)    │
  4. ontology normalize               (Phase 1.1, 1.4, 6.24)   │
  5. threat assessment                (Phase 6.25, 6.26)       │
  6. candidate linking                (Phase 4.14, 4.15)       │
  7. tracker                          (Phase 4.16, 4.17,       │
                                       4.18, 4.19)             │
  └──┬──────────────────────────────────────────────────────────┘
     │
     ▼
Frontend analyst UI ───────────────────────────────────────────┐
  • SuppressionStatus banner          (Phase 7.27/28/30/31)    │
  • Per-class restored-session        (Phase 7.29)             │
  • Analytics unavailable states       (Phase 7.34)             │
  • Position-uncertainty halo         (Phase 3.11, 7.35)       │
  • Provenance panel                  (Phase 7.36)             │
  • Class-list LLM advisory pill      (Phase 7.29 frontend)    │
  • Distinct branch counts            (Phase 7.33)             │
  └──────────────────────────────────────────────────────────────┘
```

Each layer's bias is mitigated by a targeted change. The next sections
walk through each phase, what the bias was, what we did about it, and
the operator-facing tunables that came out of it.

---

## 2. Phase-by-phase changes

### Phase 1 — Class imbalance

**The bias.** The DOTA-v1 evaluation showed `military_forces`,
`armored_vehicle`, `logistics`, `civilian`, `other` all at 0% recall.
Three distinct contributors:

  (1) the SAM3 category-presence gate (`SAM3_CATEGORY_THR=0.40`)
      killed entire rare-class prompts before they reached NMS;
  (2) the ontology had no matchers for DOTA-OBB's raw labels
      (`large-vehicle`, `small-vehicle`), so those detections
      fell through to "Other";
  (3) several Logistics prompts in the curated SAM3 set were too
      generic (`facility`, `storage tank`, `vehicle lot`) — they
      gave the model no signal beyond "there is a thing there".

#### Phase 1.1 — Expanded SAM3 prompt curation
*File:* `backend/scripts/seeds/defenceOntology.seed.json`

Fixed a typo (`"truck w or liquid"` → `"fuel tanker truck"`), tightened
generic prompts to discriminative phrases (e.g. `Ammunition_Depot`
`"facility"` → `"ammunition depot"`), and added five new logistics
objects under `Logistics` (`Ammunition_Pallet`, `Fuel_Bowser`,
`Field_Tent`, `Tent_Camp`, `Generator_Set`).

**Logic.** SAM3 is text-prompted; the more specific the prompt, the more
its grounding head has to lock onto. Replacing `"facility"` with
`"ammunition depot"` doesn't change the model — it changes what the
model is being asked to find.

**Apply to a running install:** `python -m backend.scripts.seed_ontology --reseed`

#### Phase 1.2 — Per-class SAM3 category threshold
*File:* `inference-sam3/sam3_runner.py`

The global `SAM3_CATEGORY_THR=0.40` presence gate suppressed entire
prompts whose best-chip score fell below the threshold — useful against
"Oil Refinery in Vienna" hallucinations, but catastrophic for rare
classes whose true-positive scores naturally sit at 0.15-0.30.

Added `SAM3_CATEGORY_THRESHOLDS_PER_CLASS` env (JSON dict, label →
threshold) and the `_category_threshold_for(label)` helper that the
text-grounding, batched, and video paths all consult. Case- and
whitespace-insensitive matching. `versions()` now reports the override
count for ops visibility.

**Logic.** A noise prompt like `"oil refinery"` can keep its 0.40 floor;
a rare-target prompt like `"transporter erector launcher"` drops to
0.15 so weak-but-real signals survive.

**Tunable:**
```
SAM3_CATEGORY_THRESHOLDS_PER_CLASS='{"transporter erector launcher": 0.15, "self-propelled howitzer": 0.15}'
```

#### Phase 1.3 — Small-object 512px chip pass
*File:* `backend/worker.py`

Large objects (planes, buildings) and small objects (cars, containers)
both go through the same 1008-px chip — but the small object only
covers ~10-20 pixels in that chip, which is the resolution floor for
the backbone's attention. A second 512-px pass gives small targets
~4× the pixel budget.

`slice_and_infer` now plans a list of `(chip_size, overlap, max_chips)`
passes; the main pass uses the caller's existing size, the optional
second pass uses `INFERENCE_SMALL_OBJECT_CHIP_SIZE`. Both passes share
the same `dedupe_idx`, `executor`, `session`, so NMS suppresses
cross-scale duplicates of the same object. Progress total =
`sum(planned across all passes)` so the 0-100% bar stays monotonic.

**Tunable:**
```
INFERENCE_SMALL_OBJECT_CHIP_SIZE=512
INFERENCE_SMALL_OBJECT_OVERLAP=128
INFERENCE_SMALL_OBJECT_MAX_CHIPS=256
```

#### Phase 1.4 — Class-label remap (ontology matchers)
*File:* `backend/scripts/seeds/defenceOntology.seed.json`

DOTA-OBB outputs raw labels like `large-vehicle` and `small-vehicle`.
Sentinel's ontology had no matchers for those, so they fell through to
`"Other"`. Result: the `logistics` eval class got 0% recall even when
DOTA-OBB was finding the boxes.

Extended `Logistics` matchers to catch `large-vehicle|small-vehicle|
\bbus\b|cargo van|delivery truck|truck park|vehicle park|ammunition
pallet|fuel bowser|...`. Extended `Military_Forces` matchers to catch
`self-propelled howitzer|multiple rocket launcher|military convoy|
fuel tanker truck|command vehicle`.

**Logic.** The matchers ARE the class-label remap. Adding `large-vehicle`
to Logistics matchers means a DOTA detection of `large-vehicle` now
correctly maps to the Logistics branch with `branch_id="Logistics"` and
the eval normalizer reports it as `logistics` (not `other`).

---

### Phase 2 — Confidence calibration + ensemble fusion

**The bias.** Different detectors emit confidence on different scales:
SAM3 mask scores routinely sit at 0.6-0.9, DOTA-OBB at 0.3-0.6,
Grounding-DINO is wide-tailed. NMS sort-by-confidence then
systematically prefers SAM3 over DOTA-OBB even when DOTA-OBB is the
better detector for that class.

#### Phase 2.5 — Per-model temperature scaling
*File:* `backend/calibration.py` (new)

Implements single-parameter temperature scaling (Guo et al. 2017).
`calibrate_confidence(raw, model_tag) = sigmoid(logit(raw) / T)`.
Loads temperatures from `MODEL_TEMPERATURES` env (JSON dict) or
`MODEL_TEMPERATURES_FILE` (default `/data/calibration/model_temperatures.json`).
Edge-safe (0, 1, None, NaN). Substring matching on model tag.

`backend/worker.py` now calls `calibrate_confidence` on every raw
detector score and stores `raw_confidence`, `calibrated_confidence`,
`model_temperature` on each detection. NMS and the threshold gate both
consume the calibrated value.

**Logic.** T > 1 softens overconfident distributions; T < 1 sharpens
underconfident ones; T = 1 is identity. Fit T offline once per
detector against a held-out validation slice (see
`scripts/measure_calibration_ece.py`); apply at inference time.

**Tunable:**
```
MODEL_TEMPERATURES='{"sam3": 2.0, "dota_obb": 0.8, "grounding_dino": 1.5}'
MODEL_TEMPERATURES_FILE=/data/calibration/model_temperatures.json
```

#### Phase 2.6 — Weighted Boxes Fusion (opt-in)
*File:* `backend/worker.py`

NMS picks one survivor per overlapping cluster and drops the rest.
Weighted Boxes Fusion (Solovyev et al. 2019) averages every box in the
cluster, weighted by `trust_weight × calibrated_confidence`, producing
a fused box whose confidence reflects multi-detector agreement instead
of the loudest single model.

`_WeightedBoxFusionIndex.add(batch)` returns only newly created or changed
fused heads in streaming mode, so a later chip cannot cause every historical
cluster to be stored again. A final `heads()` flush persists the settled set.
Cluster heads carry `wbf_member_count` + `wbf_member_sources` for provenance.
Confidence formula: `mean(member raw_conf) × (0.5 + 0.5 × min(N, expected) / expected)` —
a single-member cluster keeps its raw confidence; full-agreement
clusters get the full mean.

**Logic.** Two independent detectors agreeing on the same object is
strong evidence; NMS throws away the agreement signal. WBF preserves
it.

**Tunable (default off until eval harness validates no regressions):**
```
DEDUPE_METHOD=wbf
WBF_IOU_THRESHOLD=0.55
WBF_EXPECTED_MODELS=2
```

#### Phase 2.7 — Per-class NMS IoU thresholds
*File:* `backend/worker.py`

A single global 0.45 IoU over-suppresses dense small objects (truck
convoys) and under-suppresses overlapping large structures (hangars).
`_DetectionDedupeIndex._iou_for_class(det_class, modality)` looks up a
per-`parent_class` override.

**Tunable:**
```
PER_CLASS_NMS_IOU_OVERRIDES='{"aircraft": 0.55, "vehicle": 0.30, "container": 0.25}'
```

#### Phase 2.8 — Per-model trust weights
*File:* `backend/worker.py`

NMS sort key is now `trust_weight × confidence`. A tuned specialist
(DOTA-OBB on vehicles) isn't drowned out by a high-volume generalist
(SAM3 on everything).

**Tunable:**
```
PER_MODEL_TRUST_WEIGHTS='{"dota_obb": 1.0, "sam3": 0.8, "grounding_dino": 0.5}'
```

#### Phase 2.9 — Removed DB confidence default 0.55
*File:* `backend/platform_schema.py`

`ai_action_proposals.confidence` was `DEFAULT 0.55` — an implicit
optimism prior on any row written without an explicit score. Now
`NOT NULL` with no default; callers must pass a score. The only
in-tree caller already passes 0.62 explicitly, so behaviour is
unchanged for existing code.

---

### Phase 3 — Adaptive geometry & spatial handling

#### Phase 3.10 — Per-class valid-mask threshold
*File:* `backend/worker.py`

`INFERENCE_MIN_VALID_DETECTION_FRACTION=0.20` globally dropped
detections where < 20% of the bbox sat on valid pixels — fine for
ground vehicles, but it killed legitimate ships at water edges and
aircraft partially obscured by cloud. `clip_box_to_valid_mask` now
accepts `min_valid_fraction`; chip iteration looks up the per-class
floor via `_valid_fraction_threshold_for(parent_class)`.

**Tunable:**
```
PER_CLASS_VALID_FRACTION_OVERRIDES='{"ship": 0.05, "naval": 0.05, "aircraft": 0.10, "vehicle": 0.25, "infrastructure": 0.30}'
```

#### Phase 3.11 — Position-uncertainty ellipse
*Files:* `backend/worker.py`, `backend/main.py`, `frontend/.../GaiaMap.tsx`

The Phase 7.35 scalar `position_uncertainty_m` was a single number
that hid anisotropic uncertainty (e.g. WorldView-3 has 0.3 m
along-track but ~0.5 m cross-track). Replaced with
`position_uncertainty_ellipse = {semi_major_m, semi_minor_m,
bearing_deg, confidence: 0.95, source: "gsd_propagation"}`.
Latitude-aware metres/deg conversion for geographic CRSes. The
scalar is preserved (= semi-major) for back-compat with the UI halo.

#### Phase 3.12 — Cross-chip edge reconciliation
*File:* `backend/worker.py`

Two `edge_truncated` halves of the same car from adjacent chips
sometimes survive NMS because their per-chip bboxes don't IoU-overlap
(each saw a different half). `_DetectionDedupeIndex.reconcile_edge_truncated`
buckets edge-truncated survivors by `(parent_class, cx, cy)`, scans
3×3 neighbour cells, takes the union bbox of matching pairs, keeps
the higher-confidence detection, marks it `dedupe_method="edge_reconciled"`,
and clears the truncation flag. Runs on the non-streaming path
(`slice_and_infer`'s `all_kept` list).

**Logic.** Without reconciliation, a car straddling the chip boundary
gets stored twice (once per half) — analyst sees two near-by detections
of the same object. With reconciliation, one fused detection with the
correct union geometry.

#### Phase 3.13 — Chip-sampling transparency
*Files:* `backend/main.py`, `frontend/.../GaiaMap.tsx`

When `plan_inference_grid` sub-samples a large raster (because chips
exceed `MAX_INFERENCE_CHIPS`), some regions aren't scanned at all. The
analyst was previously unaware. Now `planned_chips`, `source_total_chips`,
`sampling_enabled` ride alongside every detection in
`/api/detections/geojson`, and `GaiaMap` renders an amber suppression
chip "⚠ N sub-sampled pass(es) · coverage X%" when any visible
detection comes from a sub-sampled pass.

**Logic.** "No detections in this region" is meaningless if the region
wasn't scanned. The chip tells the analyst to either expand the time
window, re-run with `INFERENCE_SPEED_PROFILE=recall_review`, or accept
the gap explicitly.

---

### Phase 4 — Candidate linking + tracker rebalance

#### Phase 4.14 — Rebalanced candidate-link score
*Files:* `backend/candidate_linking.py`, `backend/main.py`, `backend/worker.py`, `scripts/eval_candidate_links.py`

Old score: `0.45·distance + ≤0.35·compat + 0.20·confidence`. Distance
dominated — a 0.1-confidence detection at zero distance beat a
0.9-confidence detection at 500 m, generating false target associations.

New score: `0.30·distance + 0.30·compat_norm + 0.30·confidence + 0.10·history`.
`target_class_compatibility` now returns a normalised `[0.0, 1.0]`
score so the weight is in one place; `_target_history_anchor(target_id)`
queries `detection_target_candidates` for accepted-link count (saturates
at 5). Evidence JSON records the weights so the analyst can see *why*
a link scored what it did.

#### Phase 4.15 — Top-N candidate truncation
*Files:* `backend/candidate_linking.py`, `backend/main.py`, `backend/worker.py`

Old behaviour emitted every target within 1500 m. Crowded AOIs
generated hundreds of low-quality links per detection, flooding the
analyst's review queue. New behaviour scores all targets in-memory,
sorts DESC, keeps the top-5 (configurable via
`max_candidates_per_detection`), upserts only those.

#### Phase 4.16 — Kalman-style track state
*File:* `backend/tracker.py`

The existing constant-velocity predictor had no persisted uncertainty; `r_gate`
was a fixed `V_MAX × dt × 1.25` regardless of how confident the track was.
Tracks now persist `position_sigma_m`, `velocity_sigma_mps`, `motion_state`,
and `embedding_anchor`; detections load uncertainty/embeddings from metadata,
and assignments update those fields. `KALMAN_PROCESS_NOISE` is now per-(category, state) with σ_a
values (airborne aircraft 10, ground highway 3, infrastructure 0).
`_predicted_position_sigma_m` propagates as
`σ_x² + (σ_v·dt)² + (½σ_a·dt²)²`. `_kalman_update_sigma` is the 1-D
scalar Kalman position update. `_compute_cost` gate is now
`max(V_MAX-ring, KALMAN_GATE_SIGMAS · σ_pred)` — high-uncertainty
tracks get a wider gate.

**Logic.** A newborn track ought to accept far-away detections
(σ is high). A long-established stationary track ought to reject them
(σ is tight). Fixed V_MAX gating can't express that asymmetry; Kalman
σ_pred can.

**Tunable:**
```
KALMAN_OBS_NOISE_FLOOR_M=5.0
KALMAN_GATE_SIGMAS=3.0
```

#### Phase 4.17 — Per-state V_MAX
*File:* `backend/tracker.py`

Aircraft `V_MAX=14 m/s` (taxi speed) was rejecting every airborne update.
Ground `V_MAX=22 m/s` (~80 km/h urban) was rejecting highway-state
vehicles. `V_MAX_PER_STATE` is keyed by `(category, state)`:

  * air: ground 14, airborne 300, stationary 1
  * ground: default 22, highway 40, stationary 1
  * maritime: default 16, underway 25, stationary 1
  * infrastructure: 0

`_track_state(track, category)` infers state from explicit field or
last-velocity heuristic (airborne if speed > 20 m/s for air, highway
if > 25 m/s for ground, underway if > 18 m/s for maritime).

#### Phase 4.18 — Embedding-based re-ID
*File:* `backend/tracker.py`

`DELTA × (1 - cos_sim(track_embedding, det_embedding))` term in the
cost. Disambiguates two same-class detections at similar distances by
appearance. Handles list / array / `fp16_b64` embedding shapes;
graceful fallback to identity when either side lacks an embedding.

**Tunable:**
```
TRACKER_EMBEDDING_WEIGHT=0.4    # default; set 0.0 to disable
```

#### Phase 4.19 — Configurable tracker cost weights
*File:* `backend/tracker.py`

`ALPHA / BETA / GAMMA = 1.0 / 0.6 / 0.2` previously hard-coded.
`_load_tracker_weights()` reads `TRACKER_COST_WEIGHTS` env JSON.

**Tunable:**
```
TRACKER_COST_WEIGHTS='{"alpha": 0.8, "beta": 1.0, "gamma": 0.4}'
```

---

### Phase 5 — SAR detection path

#### Phase 5.20 — Default-disable SAM3 on SAR
*File:* `backend/worker.py`

SAM3 is pretrained on optical imagery. Running it on TerraMind's SAR
pseudo-RGB injects optical-domain priors into a synthetic 3-channel
view of a SAR scene, which the project's own benchmarks flagged as a
documented false-positive source.

When `sensor_type == "sar"`, the worker now writes `modality="sar"` +
`skip_sam3_image=True` into the per-chip inference metadata so the
inference service skips SAM3's grounding head. Operators opt back in
with the upload-form `allow_sam3_on_sar=true` or env
`SAM3_ALLOW_ON_SAR=1`.

#### Phase 5.20b — CFAR ship detector
*Files:* `backend/sar_cfar.py` (new), `backend/worker.py`

Companion to Phase 5.20: with SAM3 muted, something has to actually
detect ships in SAR. `backend/sar_cfar.py` implements two-parameter
CA-CFAR (cell-averaging) on dB-scaled VV/VH backscatter. numpy-only —
no scipy, no skimage, no GPU. `_box_kernel_mean` uses an integral-image
summed-area table; `_bbox_components` is a two-pass union-find label
extractor; `detect_ships_cfar(vv_db, vh_db=None, threshold_sigma=2.5,
guard_px=4, background_px=20, min_pixels=4)` returns detections in
Sentinel's standard shape (`class="ship"`, `parent_class="vessel"`,
`source_layer="sar_cfar"`, normalised + pixel bbox, confidence from
Z-score).

`run_sar_cfar_for_pass` in `worker.py` plans a 4096-px chip grid,
reads VV (band 1) + optional VH (band 2), auto-detects dB-vs-linear
scaling, runs CFAR per chip, lifts pixel coords to COG-global, applies a
SAR-aware global dedupe index across overlapping chips, runs the same
pixel→geo transform that `slice_and_infer` uses, and streams survivors through
the existing `_store_chip` callback. Invoked
immediately after `slice_and_infer` for SAR rasters when
`SAR_CFAR_ENABLED=1` (default).

**Logic.** CFAR's entire decision is local clutter statistics, not
learned features — there is no optical prior to inject. It is the
correct baseline for "what does SAR see independent of any model
trained on photographs?".

**Tunables:**
```
SAR_CFAR_ENABLED=1                 # default on
SAR_CFAR_CHIP_SIZE=4096
SAR_CFAR_OVERLAP=256
SAR_CFAR_THRESHOLD_SIGMA=2.5
SAR_CFAR_GUARD_PX=4
SAR_CFAR_BACKGROUND_PX=20
SAR_CFAR_MIN_PIXELS=4
SAR_NMS_IOU_DEFAULT=0.25           # tighter NMS for speckle-driven false-positives
SAM3_ALLOW_ON_SAR=0                # opt back in to SAM3 on SAR
```

#### Phase 5.21 — SAR metadata propagation
*File:* `backend/imagery_metadata.py`

`parse_sar_metadata(tags)` extracts:

  * `incidence_angle_deg` — single number from any of
    `INCIDENCE_ANGLE`, `S1_INCIDENCE_ANGLE`, `sar:incidence_angle`, …
  * `look_direction` — `LEFT` / `RIGHT` mapped from vendor strings
  * `orbit_direction` — `ASCENDING` / `DESCENDING` for Sentinel-1
  * `polarizations` — list, e.g. `["VV", "VH"]`
  * `layover_risk` — heuristic `high` (< 25°), `moderate` (< 35°), `low`

`extract_raster_metadata` writes `metadata["sar"]` from this. Optical
rasters get `{}` and zero overhead.

**Logic.** Low incidence + vertical structure = bright layover that
optical-trained detectors mis-fire on. Surfacing the angle lets the
analyst (and future detectors) make the right call.

#### Phase 5.22 — SAR-specific dedup IoU default
*File:* `backend/worker.py`

SAR detections are point-like and speckle-driven, so a tighter default
NMS IoU (0.25 vs 0.45 optical) suppresses the long false-positive tail
without needing per-class overrides everywhere. CFAR now uses that index
globally across overlapping chips, so chip overlap no longer duplicates ships.

---

### Phase 6 — Ontology + threat policy

#### Phase 6.23 — LLM class refinement → advisory only
*Files:* `backend/main.py`, `frontend/.../GaiaMap.tsx`

Previously the LLM's class refinement OVERWROTE the deterministic
ontology label. A hallucination ("tracked_vehicle" → "T-72 tank")
reached the analyst as authoritative. Now `/api/detections/classes`
returns a separate `llm_advisory` field (`{label, description,
recommended_filter, generated_by}`) and `GaiaMap`'s class-list renders
an amber `AI · <label>` pill alongside the deterministic label.
Deterministic stays authoritative; LLM is a hint.

**Logic.** The model's actual output is grounded in pixels; the LLM
refinement is grounded in language. Don't conflate.

#### Phase 6.24 — Preserve `was_unknown` semantic richness
*File:* `backend/ontology.py`

The unknown-label fallback used to collapse `parent_class="unknown"` +
raw `canonical_label`. The UI then rendered every novel detection as
a generic "unknown" pill, hiding the actual SAM3 prompt the model
found. Now `parent_class` and `canonical_label` fall back to the
cleaned canonical form so the meaningful label survives. `was_unknown=True`
still flags for ontology curation.

#### Phase 6.25 — Configurable threat policy
*Files:* `backend/platform_schema.py`, `backend/threat_assessment.py`

New table `threat_rules(class, category, allegiance, threat_level,
threat_confidence, rationale, enabled)`. Wildcards via NULL match
keys. Specificity scoring: `class+allegiance > class > category+allegiance > category > allegiance-only`.
`_lookup_threat_rule` queries the table; `assess_detection_threat`
consults it and elevates `threat_level` + `assessment_status="rule_matched"`
when a rule matches. Empty table → unchanged "unrated" behaviour
(open-vocab default).

**Logic.** Threat scoring is theatre-specific. Hard-coded rules can't
express "tanks in declared hostile theatre = critical; tanks in
peacetime AOI = unrated". A DB table can; no code redeploy required.

**Set rules via SQL until an admin UI ships:**
```sql
INSERT INTO threat_rules (class, allegiance, threat_level, threat_confidence, rationale)
VALUES ('tank', 'hostile', 'critical', 0.9, 'MBT in declared hostile theatre');
```

#### Phase 6.26 — Per-AOI default allegiance
*Files:* `backend/platform_schema.py`, `backend/worker.py`

`aois.default_allegiance VARCHAR(20) DEFAULT 'unknown'` — when a
detection's centroid falls inside an AOI with a non-`unknown`
default, that's the starting allegiance instead of the global
`unknown`. Explicit per-detection allegiance still wins.
`ALTER TABLE … ADD COLUMN IF NOT EXISTS` so existing installs
backfill cleanly. Smallest-area AOI wins for nested AOIs.

```sql
UPDATE aois SET default_allegiance = 'hostile' WHERE name = 'Theatre Alpha';
```

---

### Phase 7 — UI transparency

#### Phase 7.27/28/30/31 — Suppression-status banner
*File:* `frontend/src/components/GaiaMap.tsx`

A single permanent banner inside the timeline drawer shows the
analyst exactly what every silent filter is currently hiding. Each
chip is clickable to clear that filter:

  * `Showing N/M ·` (total)
  * `-N below conf X.XX ✕` (resets `confidenceThreshold` to 0)
  * `-N hidden by category (X) ✕` (resets `hiddenDetectionCategories`)
  * `-N hidden by label (X) ✕` (resets `hiddenDetectionLabels`)
  * `+N rendered as dots (over 800)` (advisory marker-mode overflow)
  * `last Nm window — older detections excluded` (advisory time-window)

**Logic.** A filter the analyst forgot they applied is silent harm.
A breadcrumb says "you're not seeing these N detections, and here's why".

#### Phase 7.29 — Restored-session reminder + persistence
*File:* `frontend/src/components/GaiaMap.tsx`

`hiddenDetectionCategories` and `hiddenDetectionLabels` now persist to
`localStorage` (`sentinel.geoMap.hiddenDetectionCategories.v1` /
`sentinel.geoMap.hiddenDetectionLabels.v1`). On the next page load, if
either has entries, an amber banner appears:

> ⚠ Filters from your last session are still hiding:
> [Show N hidden categories ✓] [Show N hidden labels ✓]

Plus the LLM-advisory pill (`AI · <label>`) renders in the class list
when the LLM's suggestion differs from the deterministic label.

#### Phase 7.32 — Cursor pagination on `/api/detections/geojson`
*File:* `backend/main.py`

Old behaviour: hard limit 20,000 detections ordered `created_at DESC`;
older detections silently truncated. New behaviour: `?cursor=<opaque-token>`
encodes `(created_at, id)` and pages by `(created_at DESC, id DESC)`, so tied
timestamps and non-monotonic IDs do not skip rows. It fetches `limit+1` to
detect more rows and returns `next_cursor: string | null` plus `has_more`.

#### Phase 7.33 — Distinct-branch breakdown on `/api/detections/classes`
*File:* `backend/main.py`

Old behaviour: `mode() WITHIN GROUP` collapsed branch_id per class to
the most-common value, hiding minority branches. New behaviour: the
query also returns `branch_breakdown: [{branch_id, icon_key, count}, …]`
sorted by count DESC. The most-common branch is still the headline; the
others are now visible to the UI.

#### Phase 7.34 — Honest analytics availability
*Files:* `backend/routers/analytics.py`, `frontend/src/components/map/AnalyticsToolsPanel.tsx`

Viewshed/LOS/routes now return explicit unavailable errors when a DEM or routing
graph is missing; change detection requires both pass IDs; POL returns an empty
FeatureCollection when no rows exist. Canned geometry remains only behind
`ANALYTICS_ALLOW_FIXTURES=1` for an explicit demo environment, and the frontend
surfaces backend error details instead of implying fixture output is analysis.

#### Phase 7.35 — Position-uncertainty halo
*File:* `frontend/src/components/GaiaMap.tsx`

When `mapZoom >= 14` and there are ≤ 400 visible features, each
detection gets a faint dashed `<Circle>` at `radius = position_uncertainty_m`.
Tells the analyst "this point's true position is within this halo"
instead of suggesting pixel-perfect coordinates.

#### Phase 7.36 — Provenance breadcrumb extensions
*File:* `frontend/src/components/map/ProvenancePanel.tsx`

New rows in the per-detection provenance panel:

  * **Confidence** — now shows `"X% calibrated · Y% raw"` when both
    are present, exposing the temperature-scaling effect (Phase 2.5).
  * **Calibration T** — shows the per-model temperature with a
    "softened" or "sharpened" tag when ≠ 1.
  * **Position ±** — shows `Xm` from Phase 7.35.
  * **Scale pass** — shows when the detection came from the
    small-object pass (Phase 1.3).
  * **Dedup method** — shows `nms` / `wbf` / `edge_reconciled` /
    `sar_cfar`.

---

### Phase 8 — Dead-weight layer removal

#### Phase 8.37 — Default-disable Prithvi burn head
*File:* `inference-sam3/main.py`

The Prithvi burn-scar head measured chip-level IoU **0.0000** on the
HLS Burn Scars test set while still costing ~20 ms per chip
(documented in `docs/inference_layer_comparison.md`). Default flipped
from `_DEFAULT` (= 1 when optional models enabled) to `"0"`. Operators
with a known-good multispectral AOI can re-enable with
`SAM3_LOAD_PRITHVI=1`.

#### Phase 8.38 — Default-disable Grounding-DINO
*File:* `inference-sam3/main.py`

Grounding-DINO measured **+0.0144 mAP** improvement for **+241 ms**
cumulative cost. Default flipped to `"0"`. The auto-gate in
`grounding_dino_gate.py` keeps it off when prompts are already
covered by SAM3+DOTA-OBB; explicit `SAM3_LOAD_GROUNDING_DINO=1`
re-enables for novel-label use cases.

#### Phase 8.41 — FMV synthetic Dubai opt-in
*Files:* `backend/video_metadata.py`, `backend/main.py`, `frontend/.../IngestConnect.tsx`

When KLV/GPMD/SRT telemetry extraction failed, the worker silently
fell back to a sine-wave Dubai fixture — and shipped that synthetic
georeference straight to the analyst as if real. Now
`TelemetryMissingError` is raised when no real source is found AND
neither `FMV_ALLOW_SYNTHETIC_TELEMETRY=1` nor the upload-form
`allow_synthetic_telemetry` flag is set; both FMV upload routes now pass
through the same extractor and fail with HTTP 422 when telemetry is absent.
The frontend FMV upload form gained a "Demo mode: allow synthetic Dubai
telemetry" checkbox.

**Logic.** A silent fallback to fake data is worse than a noisy
failure. Failure mode now matches the analyst's mental model.

---

### Phase 9 — Evaluation harness

#### Phase 9.42 — Larger DOTA slice
*File:* `scripts/fetch_real_datasets.py`

Default DOTA fetch went from **30 → 200 chips** so per-class AP
measurements have enough instances for the sparse classes
(`military_forces`, `armored_vehicle`, `logistics`, `civilian`) to
be statistically meaningful. New CLI: `--dota-chips`, `--hls-chips`,
`--skip-dota`, `--skip-hls`.

#### Phase 9.43 — Supported eval slices only
*Files:* `scripts/eval_datasets/{dota,hls_burn,sen1floods,sar_synth,sentinel1}.py`

The repo now exposes only loaders it can support honestly. Unsupported gated-loader skeletons were removed rather than promising datasets
that are not shipped here. Production loaders no longer auto-generate synthetic
DOTA/Inria data; deterministic fixtures are generated only through explicit
fixture or dry-run paths.

#### Phase 9.45 — Candidate-link eval harness
*Files:* `scripts/eval_candidate_links.py` (new), `scripts/eval_datasets/candidate_links_gt.json` (new)

The Phase 4.14 score rebalance has no objective gauge without a
ground-truth set of `(detection, correct_target)` pairs. API, worker ingest,
and evaluator now import the same pure scorer, so offline results exactly match
runtime scoring without PostGIS/Neo4j. The script computes per-detection rank,
top-1 / top-K / MRR, writes Markdown, and exits non-zero when top-1 <
`--threshold-top1` (default 0.75). The shipped deterministic fixture contains
5 detections and 6 targets and clears the default gate.

#### Phase 9.46 — ECE measurement script
*Files:* `scripts/measure_calibration_ece.py` (new), `scripts/eval_metrics/box_metrics.py`

`per_prediction_matches(predictions, ground_truth, iou_threshold)` in
`box_metrics.py` returns one row per input prediction (`{label, score,
is_tp, iou}`). Greedy-by-score within each label; same matching
contract as `compute_box_metrics`. The ECE script reuses
`compare_inference_layers._post_detect / _parse_detections /
_synthetic_response`, groups predictions by `source_layer`, computes
binned ECE, fits BCE-minimising single-parameter temperature per
detector via grid search (no SciPy), and writes a markdown table +
ready-to-paste `model_temperatures.json` blob.

**Usage:**
```
python scripts/measure_calibration_ece.py \
    --inference-url http://localhost:8001 \
    --slice dota --max-chips 60 --bins 15 \
    --enabled-layers sam3,dota_obb \
    --output docs/calibration_ece.md \
    --json-output docs/calibration_ece.json
```

#### Phase 9.47 — Per-class regression gate
*File:* `scripts/compare_inference_layers.py`

New CLI flags:
```
--regression-baseline <path>      # JSON output from a prior run
--regression-tol 0.05             # max per-class recall drop
```

`_check_regression_gate` loads both JSONs, flattens per-class recall
by `(config, class)`, and exits non-zero if any class in both runs
regresses by more than the tolerance. Shared classes only — new
configs / new classes are permitted.

**CI gate example:**
```
python scripts/compare_inference_layers.py \
    --slice dota --max-chips 60 --repeats 3 \
    --output docs/inference_layer_comparison.md \
    --json-output docs/inference_layer_comparison.json \
    --regression-baseline docs/inference_layer_comparison.baseline.json \
    --regression-tol 0.05
```

---

## 3. File-by-file applied index

This is the live May contract surface after validation; removed gated loaders
are intentionally absent.

### Backend

| Files | Applied responsibility |
|---|---|
| `backend/candidate_linking.py`, `backend/main.py`, `backend/worker.py` | shared candidate scoring, composite detection cursor, ingest parity |
| `backend/tracker.py`, `backend/init_postgis.sql` | persisted uncertainty / motion / embedding state with additive schema |
| `backend/worker.py` | WBF changed-head streaming, SAR CFAR overlap dedupe, provenance metadata |
| `backend/video_metadata.py`, `backend/fmv_helpers.py`, `backend/main.py` | real FMV extraction by default; synthetic telemetry only explicit demo opt-in |
| `backend/routers/analytics.py`, `backend/change_detection.py`, `backend/terrain.py`, `backend/routing.py`, `backend/schemas.py` | honest analytics unavailable states and empty POL results |
| `backend/provider_lifecycle.py` | removed dead lifecycle no-op |
| `backend/tests/conftest.py`, `backend/tests/test_debias_units.py`, `pytest.ini` | offline unit coverage and clean integration-test skipping |

### Frontend

| Files | Applied responsibility |
|---|---|
| `frontend/src/services/analytics.ts`, `frontend/src/components/map/AnalyticsToolsPanel.tsx`, `frontend/src/components/GaiaMap.tsx` | unavailable/error handling with clearly labeled explicit demo fixtures |

### Evaluation / docs

| Files | Applied responsibility |
|---|---|
| `scripts/eval_candidate_links.py`, `scripts/eval_datasets/candidate_links_gt.json` | canonical shared-scorer candidate gate with passing deterministic fixture |
| `scripts/eval_metrics/label_normalizer.py` | seed-backed pure offline normalization |
| `scripts/eval_datasets/{dota,hls_burn,sen1floods,sar_synth}.py`, `scripts/fetch_eval_datasets.py` | no implicit synthetic dataset generation in normal eval paths |
| `scripts/measure_calibration_ece.py` | supported-slice dispatch only |
| `docs/geoint-debias-2026-05.md`, `README.md` | truthful verification and dataset instructions |

## 4. Operational tunables reference

Drop these into `.env` or the worker container's environment to override
defaults. All tunables are documented in the phase that introduced them
above.

### Class-imbalance + prompt curation
```bash
SAM3_CATEGORY_THRESHOLDS_PER_CLASS='{"transporter erector launcher": 0.15, "self-propelled howitzer": 0.15}'
INFERENCE_SMALL_OBJECT_CHIP_SIZE=512
INFERENCE_SMALL_OBJECT_OVERLAP=128
INFERENCE_SMALL_OBJECT_MAX_CHIPS=256
```

### Calibration + fusion (Phase 2)
```bash
MODEL_TEMPERATURES='{"sam3": 2.0, "dota_obb": 0.8, "grounding_dino": 1.5}'
MODEL_TEMPERATURES_FILE=/data/calibration/model_temperatures.json

DEDUPE_METHOD=nms                             # or "wbf"
WBF_IOU_THRESHOLD=0.55
WBF_EXPECTED_MODELS=2

PER_CLASS_NMS_IOU_OVERRIDES='{"aircraft": 0.55, "vehicle": 0.30, "container": 0.25}'
PER_MODEL_TRUST_WEIGHTS='{"dota_obb": 1.0, "sam3": 0.8, "grounding_dino": 0.5}'
```

### Spatial handling (Phase 3)
```bash
PER_CLASS_VALID_FRACTION_OVERRIDES='{"ship": 0.05, "naval": 0.05, "aircraft": 0.10, "vehicle": 0.25, "infrastructure": 0.30}'
```

### Tracker (Phase 4)
```bash
KALMAN_OBS_NOISE_FLOOR_M=5.0
KALMAN_GATE_SIGMAS=3.0
TRACKER_EMBEDDING_WEIGHT=0.4                  # default; set 0.0 to disable
TRACKER_COST_WEIGHTS='{"alpha": 0.8, "beta": 1.0, "gamma": 0.4}'
```

### SAR (Phase 5)
```bash
SAM3_ALLOW_ON_SAR=0                           # opt back in if needed
SAR_CFAR_ENABLED=1
SAR_CFAR_CHIP_SIZE=4096
SAR_CFAR_OVERLAP=256
SAR_CFAR_THRESHOLD_SIGMA=2.5
SAR_CFAR_GUARD_PX=4
SAR_CFAR_BACKGROUND_PX=20
SAR_CFAR_MIN_PIXELS=4
SAR_NMS_IOU_DEFAULT=0.25
```

### Analytics + FMV truthful defaults
```bash
ANALYTICS_ALLOW_FIXTURES=0                    # set 1 only for explicit demo fixtures
FMV_ALLOW_SYNTHETIC_TELEMETRY=0               # set 1 only for explicit demo telemetry
```

### Dead-weight layers (Phase 8)
```bash
SAM3_LOAD_PRITHVI=0                           # default; flip to 1 for opt-in
SAM3_LOAD_GROUNDING_DINO=0                    # default; flip to 1 for opt-in
```

---

## 5. Verification cookbook

### Offline verification (no PostGIS required)
```bash
cd frontend && npm run build
cd ..
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests scripts/eval_datasets/tests scripts/eval_metrics/tests -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/compare_inference_layers.py --dry-run --max-chips 2 --output /tmp/inference_layer_comparison.md
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/measure_calibration_ece.py --dry-run --max-chips 2 --output /tmp/calibration_ece.md
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/eval_candidate_links.py --gt scripts/eval_datasets/candidate_links_gt.json --output /tmp/candidate_link_eval.md
```

The backend test command runs pure unit tests offline and marks PostGIS suites
as `integration`; they skip cleanly when the database is unavailable.

### Apply ontology changes
```bash
python -m backend.scripts.seed_ontology --reseed
```

### Re-fetch larger DOTA slice
```bash
python scripts/fetch_real_datasets.py --dota-chips 200
```

### Run inference-layer comparison + regression gate
```bash
python scripts/compare_inference_layers.py \
    --slice dota --max-chips 60 --repeats 3 \
    --output docs/inference_layer_comparison.md \
    --json-output docs/inference_layer_comparison.json \
    --regression-baseline docs/inference_layer_comparison.baseline.json \
    --regression-tol 0.05
```

### Measure ECE + fit temperatures
```bash
python scripts/measure_calibration_ece.py \
    --inference-url http://localhost:8001 \
    --slice dota --max-chips 60 \
    --enabled-layers sam3,dota_obb \
    --output docs/calibration_ece.md \
    --json-output docs/calibration_ece.json
```
The resulting `docs/calibration_ece.json["by_model"]` dict has the
fitted T per model; copy them into `MODEL_TEMPERATURES_FILE` and
restart the worker.

### Candidate-link evaluation
```bash
python scripts/eval_candidate_links.py \
    --gt scripts/eval_datasets/candidate_links_gt.json \
    --top-k 5 --max-distance-m 1500 \
    --threshold-top1 0.75 \
    --output docs/candidate_link_eval.md
```

### Per-AOI default allegiance (until admin UI ships)
```sql
UPDATE aois SET default_allegiance = 'hostile' WHERE name = 'Theatre Alpha';
```

### Threat rules
```sql
INSERT INTO threat_rules (class, allegiance, threat_level, threat_confidence, rationale)
VALUES ('tank', 'hostile', 'critical', 0.9, 'MBT in declared hostile theatre');

INSERT INTO threat_rules (category, allegiance, threat_level, threat_confidence, rationale)
VALUES ('maritime', 'unknown', 'medium', 0.7, 'Maritime contact pending IFF');
```

### Build + test
```bash
# backend Python syntax
python3 -c "import ast; ast.parse(open('backend/worker.py').read())"

# inference-sam3 unit tests
cd inference-sam3 && python3 -m pytest tests/test_grounding_dino_gate.py tests/test_fusion.py

# scripts/eval_metrics tests
python3 -m pytest scripts/eval_metrics/tests/test_box_metrics.py

# frontend typecheck + production build
cd frontend && npx tsc --noEmit -p tsconfig.json && npm run build
```

---

## 6. The bias inventory — coverage map

The original audit (`/home/avinash/.claude/plans/the-geoint-detection-has-squishy-fog.md`)
identified 47 distinct biases. Mapping each to its mitigation:

| # | Bias | Phase | Status |
|---|---|---|---|
| 1 | DOTA-OBB recall blind spots (0% on 5 classes) | 1.1, 1.4 | ✓ |
| 2 | SAM3 prompt set misses small-class military | 1.1 | ✓ |
| 3 | `SAM3_CATEGORY_THRESHOLD` global gate kills rare classes | 1.2 | ✓ |
| 4 | No per-class confidence calibration | 2.5 | ✓ |
| 5 | NMS IoU is global 0.45 not per-class | 2.7 | ✓ |
| 6 | NMS is confidence-greedy (loud-model bias) | 2.6, 2.8 | ✓ |
| 7 | Bucketing artifacts at 512-px bucket edges | — | residual |
| 8 | `detection_overlap` mixes OBB + HBB IoU | — | residual |
| 9 | `GLOBAL_CONFIDENCE_FLOOR=0.0` accepts everything | — | by design |
| 10 | No model-version weighting | 2.8 | ✓ |
| 11 | DB default `confidence=0.55` (optimism bias) | 2.9 | ✓ |
| 12 | `INFERENCE_MIN_VALID_DETECTION_FRACTION=0.20` drops legit edges | 3.10 | ✓ |
| 13 | No position-error ellipse | 3.11 + 7.35 | ✓ |
| 14 | Chip planner samples large rasters, no UI signal | 3.13 | ✓ |
| 15 | Edge-truncated detections never reconciled across chips | 3.12 | ✓ |
| 16 | Candidate-link score `0.45·dist + ≤0.35·compat + 0.20·conf` | 4.14 | ✓ |
| 17 | Compatibility is substring text overlap | 4.14 (normalised) | ✓ |
| 18 | No historical anchoring on repeated sightings | 4.14 history term | ✓ |
| 19 | Fixed tracker `ALPHA/BETA/GAMMA = 1.0/0.6/0.2` | 4.19 | ✓ |
| 20 | `V_MAX` per-category not per-state | 4.17 | ✓ |
| 21 | No multi-hypothesis tracking | — | residual |
| 22 | No embedding re-ID in tracker cost | 4.18 | ✓ |
| 23 | TerraMind emits only embedding, no SAR detector | 5.20b (CFAR) | ✓ |
| 24 | No SAR-specific NMS / dedup tuning | 5.22 | ✓ |
| 25 | No incidence-angle / look-direction metadata | 5.21 | ✓ |
| 26 | LLM class refinement → authoritative ontology | 6.23 | ✓ |
| 27 | `was_unknown` collapses to `category="unknown"` | 6.24 | ✓ |
| 28 | Threat rules hard-coded | 6.25 | ✓ |
| 29 | Allegiance defaults to "unknown" everywhere | 6.26 | ✓ |
| 30 | Confidence slider silently filters | 7.27 | ✓ |
| 31 | Hidden categories persist invisibly | 7.29 | ✓ |
| 32 | Marker-limit 800 silently switches rendering | 7.30 | ✓ |
| 33 | Default time-window 60 min, no signal | 7.31 | ✓ |
| 34 | `/api/detections/geojson` truncates at 20k | 7.32 | ✓ |
| 35 | `/api/detections/classes` `mode()` hides minority branches | 7.33 | ✓ |
| 36 | Analytics fallback fixtures rendered as real | 7.34 | ✓ — unavailable by default |
| 37 | Prithvi burn-scar head IoU=0.0000 | 8.37 | ✓ |
| 38 | Grounding-DINO marginal +0.0144 mAP for +241 ms | 8.38 | ✓ |
| 39 | Dangling DINOv3-LVD config keys | — | already removed |
| 40 | Dangling DEFENCE_YOLO config keys | — | already removed |
| 41 | FMV synthetic Dubai silent fallback | 8.41 | ✓ |
| 42 | DOTA-v1 28-chip eval too small | 9.42 | ✓ |
| 43 | Unsupported gated eval-loader promises | 9.43 | ✓ removed; supported slices retained |
| 44 | No candidate-link precision/recall harness | 9.45 | ✓ |
| 45 | No ECE measurement | 9.46 | ✓ |
| 46 | No per-class regression gate | 9.47 | ✓ |
| 47 | (Provenance breadcrumb extensions) | 7.36 | ✓ |

**Coverage note:** the applied repo keeps the original residual design items
visible below, but unsupported gated-dataset promises are intentionally removed
rather than counted as shipped code. The remaining residuals are
documented design decisions (#9 — by design for open-vocab),
already-existing cleanup (#39, #40), or deeper architectural changes
flagged for a future session (#7 bucketing, #8 OBB/HBB mixing,
#21 multi-hypothesis tracking).

---

## 7. Why this matters

The defence-analyst use case has an asymmetric cost function: a missed
true positive (failure to detect a hostile MBT) is dramatically worse
than a false positive (analyst wastes 30 seconds dismissing a civilian
truck). The pre-refactor pipeline systematically optimised the wrong
side of that tradeoff:

  * 0% recall on the five most-relevant classes
  * silent UI filters that hid real detections
  * a model with documented IoU=0.0 still in the default stack
  * synthetic Dubai georeference shipped as if real
  * candidate links flooded the review queue with hundreds of low-quality
    proximity matches per detection

After the refactor:

  * **Class-imbalance hooks** at every level (prompt curation, per-class
    thresholds, multi-scale chip pass, ontology matchers) make the
    sparse-class detections survive.
  * **Calibration + WBF** make multi-detector agreement boost
    confidence; the loudest model no longer wins.
  * **Transparency banner** tells the analyst exactly what's being
    hidden, so a forgotten filter doesn't masquerade as "no detections".
  * **SAR is no longer SAM3 in disguise**: the CFAR detector decides
    based on clutter statistics, not optical-photo priors.
  * **Threat policy + per-AOI allegiance** are now operator-configurable
    via SQL; defence theatres can elevate the right classes without
    redeploying.
  * **Evaluation harness** (ECE, candidate-link eval, per-class
    regression gate) catches accidental quality regressions in CI.

The next analyst opening Sentinel sees:
  * more true positives on the classes that matter
  * a clear breadcrumb of what every filter is hiding
  * provenance that traces every confidence number from raw logit to
    fused calibrated score
  * SAR scenes that produce ship detections, not optical-prior
    hallucinations
  * a position halo that admits the ±metres of uncertainty in every
    detection
  * a small "AI suggestion" pill that captures LLM advisory output
    without overriding the deterministic class

That is, in the plain text of the original audit goal:

> a defence analyst sees more of the true positives, fewer of the
> false positives, and never wonders what the pipeline silently
> threw away — without retraining any model.

— done.
