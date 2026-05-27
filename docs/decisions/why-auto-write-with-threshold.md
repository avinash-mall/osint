**Decision:** When the top-1 reference-platform candidate has cosine score ≥ `REFERENCE_ID_AUTO_THRESHOLD` (default 0.85), auto-write `platform_name` / `platform_family` / `platform_confidence` / `platform_source='auto'` to `object_details`. Below threshold, leave `object_details` untouched and the candidates land as `status='pending'` for analyst review.

## Why
- **Save analyst time on confident matches.** At threshold 0.85 on unit-normalised DINOv3-SAT embeddings, a top-1 match is extremely unlikely to be wrong; auto-writing prevents the analyst from having to confirm the obvious.
- **Audit trail is preserved.** Every candidate row (auto-applied or pending) carries `score`, `rank`, `matched_chip_ids`. An analyst can always retrace what drove the auto-apply decision.
- **Threshold is operator-tunable.** Defence analyst sites with stricter requirements set `REFERENCE_ID_AUTO_THRESHOLD=0.95`; sites with looser standards lower it. Note: read at worker process start; a .env edit requires `docker compose restart worker` to take effect.
- **`platform_source='auto'` makes the provenance visible.** UI surfaces (Plan D) can render auto-applied rows differently (e.g. lower visual weight) so an analyst always sees what was machine-asserted vs analyst-asserted.

## What we rejected
- **Always-pending, never auto-write.** Would mean an analyst has to click-approve even when the top-1 score is 0.99 against a 1000-chip-strong reference. Wastes time without improving safety meaningfully.
- **Threshold = 1.0 (exact-match only).** Too strict; fp16 round-trip noise alone is ~5e-4, so even a self-vs-self lookup would rarely hit exactly 1.0. 0.85 is the sweet spot per the parent spec.

## Auto vs analyst conflict policy
The auto-apply UPSERT uses plain `EXCLUDED.X` (no COALESCE) on the four `platform_*` columns. This means a subsequent auto-identify run on the same detection WILL clobber a prior analyst-asserted `platform_name`. **This is intentional**:
- The `attach_identification_candidates` helper is called only from the detection-INSERT path today, so a "later auto run" only happens if the detection is re-processed (e.g. an analyst triggered a re-detect on a pass). In that case, the freshest model verdict is the most useful default.
- The `platform_source='auto'` discriminator lets Plan D's UI surface the conflict explicitly. An analyst who wants to lock their assertion against future auto re-runs can edit their record post-auto and Plan D may eventually add a "lock" affordance.
- The candidate-row audit trail (`status='auto_applied'`, `applied_at`, `score`) preserves the full decision history even if `object_details` gets rewritten.
- If preserving analyst values across re-runs becomes a hard requirement, the auto path's SET clause should switch to `CASE WHEN object_details.platform_source = 'analyst' THEN object_details.X ELSE EXCLUDED.X END` per column.

## Consequences
- `object_details.platform_source` carries the discriminator; downstream consumers can branch on it.
- A bug in auto-apply could silently mis-label many detections at once. The mitigation is the CHECK constraint added in Task 1 (`platform_confidence ∈ [0, 1]`) plus the visible `platform_source='auto'` flag, plus the audit-trail candidates table.
- Re-baking the reference DB (e.g. switching to a richer xView seed) will NOT retroactively change existing `object_details.platform_*` rows. A separate "refresh identifications" maintenance task in a future plan is the right tool for that.
